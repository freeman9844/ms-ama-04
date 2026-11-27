import io
import json
import zipfile

import pytest

from ops.azcli import OpsError
from ops.cli import main
from ops.deploy import EGRESS_RULE, PIP_PLATFORMS, deploy, deploy_function

ENV = {
    "AZURE_SUBSCRIPTION_ID": "sub-1",
    "AZURE_RESOURCE_GROUP": "rg-amalend",
    "ACR_NAME": "acrx",
    "AZURE_CONTAINER_REGISTRY_ENDPOINT": "acrx.azurecr.io",
    "FUNCTION_APP_NAME": "func-x",
    "OPERATOR_IP": "20.30.40.50",
    "ACR_PUBLIC": "true",
    "MOCK_CONSENT_SECRET": "m" * 64,
    "PSEUDONYM_KEY": "p" * 64,
}
APPS = {"POLICY_APP_NAME": "ca-policy", "MOCKAPI_APP_NAME": "ca-mockapi", "CONSOLE_APP_NAME": "ca-console",
        "EVAL_JOB_NAME": "job-eval", "OPS_JOB_NAME": "job-ops"}
ZIP_DEPLOY = ("az", "functionapp", "deployment", "source", "config-zip")
PIP = ("-m", "pip", "install")


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    # 실제 SCM 호출과 운영자 PC의 패키지 캐시를 건드리지 않습니다.
    monkeypatch.setattr("ops.deploy.scm_forbidden_ip", lambda name: None)
    monkeypatch.setenv("AMALEND_OPS_CACHE", str(tmp_path / "pip-cache"))


def running_image(image):
    return json.dumps({"properties": {"template": {"containers": [{"image": image}]}}})


def pip_calls(runner):
    return [c for c in runner.calls if tuple(c[1:4]) == PIP]


@pytest.fixture
def repo(tmp_path):
    files = {
        "src/orchestrator/function_app.py": "app = None\n",
        "src/orchestrator/host.json": "{}\n",
        "src/orchestrator/requirements.txt": "pydantic==2.14.1\n",
        "src/orchestrator/journey/__init__.py": "",
        "src/orchestrator/journey/__pycache__/models.cpython-312.pyc": "x",
        "src/orchestrator/local.settings.json": "{}",
        "src/agents/__init__.py": "",
        "src/agents/features.py": "",
        "src/shared/__init__.py": "",
        "src/shared/contracts.py": "",
    }
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def capture_zip(runner):
    """config-zip 호출 순간의 zip 내용을 기록합니다(임시 폴더는 배포 후 지워짐)."""
    seen: dict[str, set[str]] = {}

    def wrapped(args, *, stream=False):
        if tuple(args[:5]) == ZIP_DEPLOY:
            with zipfile.ZipFile(args[args.index("--src") + 1]) as archive:
                seen["names"] = set(archive.namelist())
        return runner(args, stream=stream)

    return wrapped, seen


def test_images_need_acr_build_window(fake_runner, repo):
    runner = fake_runner({**ENV, "ACR_PUBLIC": "false"})
    with pytest.raises(OpsError, match="--acr"):
        deploy(runner, "policy", root=repo, tag="t1")
    assert runner.commands("az", "acr") == []
    assert runner.commands("azd", "provision") == []


def test_policy_image_built_recorded_then_provisioned(fake_runner, repo):
    runner = fake_runner(ENV)
    deploy(runner, "policy", root=repo, tag="t1")
    [build] = runner.commands("az", "acr", "build")
    assert build[build.index("-r") + 1] == "acrx"
    assert build[build.index("-t") + 1] == "policy:t1"
    assert build[build.index("-f") + 1] == "src/policy/Dockerfile"
    assert str(repo) in build
    assert build[-2:] == ["--subscription", "sub-1"]
    assert runner.env["SERVICE_POLICY_IMAGE_NAME"] == "acrx.azurecr.io/policy:t1"
    assert "SERVICE_MOCKAPI_IMAGE_NAME" not in runner.env
    assert runner.index("az", "acr", "build") < runner.index("azd", "provision")
    assert runner.commands(*ZIP_DEPLOY) == []


def test_function_needs_open_window(fake_runner, repo):
    runner = fake_runner({**ENV, "OPERATOR_IP": "closed"})
    with pytest.raises(OpsError, match="window open"):
        deploy(runner, "orchestrator", root=repo, tag="t1")
    assert runner.calls == [["azd", "env", "get-values"]]


def test_function_package_layout_and_linux_wheels(fake_runner, repo):
    runner = fake_runner(ENV)
    wrapped, seen = capture_zip(runner)
    deploy(wrapped, "orchestrator", root=repo, tag="t1")

    [pip] = pip_calls(runner)
    assert "pip-cache" in pip[pip.index("--target") + 1]
    assert [pip[i + 1] for i, a in enumerate(pip) if a == "--platform"] == list(PIP_PLATFORMS)
    assert "--only-binary=:all:" in pip and pip[pip.index("--python-version") + 1] == "3.12"

    assert {"function_app.py", "host.json", "requirements.txt", "journey/__init__.py",
            "agents/features.py", "shared/contracts.py"} <= seen["names"]
    assert not any(n.endswith(".pyc") or "__pycache__" in n or n == "local.settings.json"
                   for n in seen["names"])
    [zip_deploy] = runner.commands(*ZIP_DEPLOY)
    assert zip_deploy[zip_deploy.index("-n") + 1] == "func-x"
    assert zip_deploy[zip_deploy.index("--build-remote") + 1] == "false"
    assert runner.commands("azd", "provision") == []


def test_all_builds_every_image_provisions_once_then_deploys_function(fake_runner, repo):
    runner = fake_runner(ENV)
    deploy(runner, "all", root=repo, tag="t2")
    tags = [c[c.index("-t") + 1] for c in runner.commands("az", "acr", "build")]
    assert tags == ["policy:t2", "mockapi:t2", "console:t2", "eval:t2"]
    assert runner.env["SERVICE_MOCKAPI_IMAGE_NAME"] == "acrx.azurecr.io/mockapi:t2"
    assert runner.env["SERVICE_CONSOLE_IMAGE_NAME"] == "acrx.azurecr.io/console:t2"
    assert len(runner.commands("azd", "provision")) == 1
    assert runner.index("azd", "provision") < runner.index(*ZIP_DEPLOY)


def test_cli_deploy(fake_runner, repo, monkeypatch):
    monkeypatch.setattr("ops.deploy.ROOT", repo)
    runner = fake_runner(ENV)
    out = io.StringIO()
    assert main(["deploy", "mockapi"], runner=runner, out=out) == 0
    assert runner.env["SERVICE_MOCKAPI_IMAGE_NAME"].startswith("acrx.azurecr.io/mockapi:")
    assert "mockapi" in out.getvalue()


def test_console_image_built_from_repo_root(fake_runner, repo):
    # 콘솔 이미지는 src/agents·src/shared·journey 모델을 함께 복사하므로 저장소 루트가 빌드 컨텍스트입니다.
    runner = fake_runner(ENV)
    assert deploy(runner, "console", root=repo, tag="t3") == ["acrx.azurecr.io/console:t3"]
    [build] = runner.commands("az", "acr", "build")
    assert build[build.index("-f") + 1] == "src/console/Dockerfile"
    assert str(repo) in build
    assert runner.commands(*ZIP_DEPLOY) == []


def test_eval_image_built_from_repo_root(fake_runner, repo):
    runner = fake_runner(ENV)
    assert deploy(runner, "eval", root=repo, tag="t4") == ["acrx.azurecr.io/eval:t4"]
    [build] = runner.commands("az", "acr", "build")
    assert build[build.index("-f") + 1] == "src/evals/Dockerfile"
    assert runner.env["SERVICE_EVAL_IMAGE_NAME"] == "acrx.azurecr.io/eval:t4"
    assert runner.commands(*ZIP_DEPLOY) == []

def test_image_swapped_without_provision_when_app_already_runs_acr_image(fake_runner, repo):
    runner = fake_runner({**ENV, **APPS}).on("az", "containerapp", "show",
                                            results=[running_image("acrx.azurecr.io/console:old")])
    assert deploy(runner, "console", root=repo, tag="t5") == ["acrx.azurecr.io/console:t5"]
    [update] = runner.commands("az", "containerapp", "update")
    assert update[update.index("-n") + 1] == "ca-console"
    assert update[update.index("--image") + 1] == "acrx.azurecr.io/console:t5"
    assert update[-2:] == ["--subscription", "sub-1"]
    assert runner.env["SERVICE_CONSOLE_IMAGE_NAME"] == "acrx.azurecr.io/console:t5"
    assert runner.index("az", "acr", "build") < runner.index("az", "containerapp", "update")
    assert runner.commands("azd", "provision") == []


def test_eval_image_swaps_both_jobs(fake_runner, repo):
    runner = fake_runner({**ENV, **APPS}).on("az", "containerapp", "job", "show",
                                            results=[running_image("acrx.azurecr.io/eval:old")])
    deploy(runner, "eval", root=repo, tag="t6")
    names = [c[c.index("-n") + 1] for c in runner.commands("az", "containerapp", "job", "update")]
    assert names == ["job-eval", "job-ops"]
    assert runner.commands("azd", "provision") == []


@pytest.mark.parametrize("env, image", [
    ({**ENV, **APPS}, "mcr.microsoft.com/azurelinux/base/python:3.12"),  # 자리표시: 레지스트리 설정이 없습니다.
    (ENV, "acrx.azurecr.io/console:old"),  # 앱 이름이 아직 azd 환경에 없습니다.
])
def test_image_deploy_falls_back_to_provision(fake_runner, repo, env, image):
    runner = fake_runner(env).on("az", "containerapp", "show", results=[running_image(image)])
    deploy(runner, "console", root=repo, tag="t7")
    assert runner.commands("az", "containerapp", "update") == []
    assert len(runner.commands("azd", "provision")) == 1


def test_cli_deploy_provision_flag_forces_full_provision(fake_runner, repo, monkeypatch):
    monkeypatch.setattr("ops.deploy.ROOT", repo)
    runner = fake_runner({**ENV, **APPS}).on("az", "containerapp", "show",
                                            results=[running_image("acrx.azurecr.io/console:old")])
    assert main(["deploy", "console", "--provision"], runner=runner, out=io.StringIO()) == 0
    assert runner.commands("az", "containerapp", "update") == []
    assert len(runner.commands("azd", "provision")) == 1


def test_function_packages_reused_until_requirements_change(fake_runner, repo):
    runner = fake_runner(ENV)
    deploy(runner, "orchestrator", root=repo, tag="t1")
    deploy(runner, "orchestrator", root=repo, tag="t1")
    assert len(pip_calls(runner)) == 1
    (repo / "src/orchestrator/requirements.txt").write_text("pydantic==2.15.0\n", encoding="utf-8")
    deploy(runner, "orchestrator", root=repo, tag="t1")
    assert len(pip_calls(runner)) == 2


def test_failed_pip_install_is_not_cached(fake_runner, repo):
    runner = fake_runner(ENV)
    calls = {"n": 0}

    def flaky(args, *, stream=False):
        if tuple(args[1:4]) == PIP:
            calls["n"] += 1
            if calls["n"] == 1:
                raise OpsError("pip boom")
        return runner(args, stream=stream)

    with pytest.raises(OpsError, match="pip boom"):
        deploy(flaky, "orchestrator", root=repo, tag="t1")
    deploy(flaky, "orchestrator", root=repo, tag="t1")
    assert calls["n"] == 2
    assert len(runner.commands(*ZIP_DEPLOY)) == 1


def test_scm_403_adds_temporary_egress_rule_then_removes_it(fake_runner, repo):
    runner = fake_runner(ENV)
    answers = iter(["167.220.233.25", "167.220.233.25", None])
    waits = []
    deploy_function(runner, repo, probe=lambda name: next(answers), sleep=waits.append)
    [add] = runner.commands("az", "functionapp", "config", "access-restriction", "add")
    assert add[add.index("--rule-name") + 1] == EGRESS_RULE
    assert add[add.index("--ip-address") + 1] == "167.220.233.25/32"
    assert add[add.index("--priority") + 1] == "110"
    assert waits == [5.0]
    add_at = runner.index("az", "functionapp", "config", "access-restriction", "add")
    zip_at = runner.index(*ZIP_DEPLOY)
    remove_at = runner.index("az", "functionapp", "config", "access-restriction", "remove")
    assert add_at < zip_at < remove_at


def test_scm_egress_rule_removed_even_when_zip_deploy_fails(fake_runner, repo):
    runner = fake_runner(ENV).on(*ZIP_DEPLOY, results=[OpsError("zip boom")])
    answers = iter(["167.220.233.25", None])
    with pytest.raises(OpsError, match="zip boom"):
        deploy_function(runner, repo, probe=lambda name: next(answers), sleep=lambda s: None)
    assert len(runner.commands("az", "functionapp", "config", "access-restriction", "remove")) == 1


def test_scm_reachable_needs_no_egress_rule(fake_runner, repo):
    runner = fake_runner(ENV)
    deploy_function(runner, repo, probe=lambda name: None, sleep=lambda s: None)
    assert runner.commands("az", "functionapp", "config", "access-restriction") == []
    assert len(runner.commands(*ZIP_DEPLOY)) == 1
