import pytest
from fastapi.testclient import TestClient

from console.app import create_app
from consolepreview.fixtures import build_deps


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app(build_deps()), follow_redirects=False, cookies={"ama_locale_v2": "ko"})


@pytest.mark.parametrize("path", [
    "/",
    "/applications/rows",
    "/applications/app-20261103-p01",
    "/applications/app-20261103-p06",
    "/loans",
    "/loans/app-20261103-p03",
    "/loans/app-20261103-p04",
    "/loans/app-20261103-p11",
    "/ops",
    "/grievances",
    "/borrower",
])
def test_preview_pages_open(client, path):
    assert client.get(path).status_code == 200


def test_offline_english_opinion_explains_evidence_and_next_check(client):
    html = client.get("/applications/app-20261103-p01?lang=en").text
    opinion = html.split('<section id="tab-opinion"', 1)[1].split('id="tab-evidence"', 1)[0]
    assert "cash buffer" in opinion.lower()
    assert "Compare settlement statements with bank deposits." in opinion
    assert "정산 명세와 계좌 입금을 대조하세요." not in opinion
