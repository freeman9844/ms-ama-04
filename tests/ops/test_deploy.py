import io
import zipfile

import pytest

from ops.azcli import OpsError
from ops.cli import main
from ops.deploy import PIP_PLATFORMS, deploy

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
ZIP_DEPLOY = ("az", "functionapp", "deployment", "source", "config-zip")


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

    [pip] = [c for c in runner.calls if c[1:4] == ["-m", "pip", "install"]]
    assert pip[pip.index("--target") + 1].replace("\\", "/").endswith(".python_packages/lib/site-packages")
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
