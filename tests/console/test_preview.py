import pytest
from fastapi.testclient import TestClient

from console.app import create_app
from consolepreview.fixtures import build_deps


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app(build_deps()), follow_redirects=False)


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
