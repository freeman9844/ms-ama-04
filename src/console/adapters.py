"""콘솔의 Azure 어댑터(관리 ID). Azure SDK는 지연 import한다.

- Cosmos `applications`: 읽기 전용(Built-in Data Reader)
- Blob 원천 데이터: 읽기 전용(실명 복원용)
- Storage Queue: applications-start·officer-decisions에 보내기만 한다(Functions host.json messageEncoding=none)
"""

import json
import os
from collections.abc import Callable, Mapping
from typing import Any

from console.app import ConsoleDeps

APPLICATIONS = "applications"
PSEUDONYM_SECRET = "pseudonym-key"
LIST_QUERY = (
    "SELECT TOP @limit c.id, c.applicant_id, c.status, c.requested_amount_krw, c.updated_at, "
    "c.assessment.recommendation AS recommendation FROM c ORDER BY c.updated_at DESC"
)


class CosmosApplications:
    def __init__(self, container: Any) -> None:
        self._container = container

    def list_recent(self, limit: int) -> list[dict[str, Any]]:
        items = self._container.query_items(
            query=LIST_QUERY, parameters=[{"name": "@limit", "value": limit}], enable_cross_partition_query=True
        )
        return list(items)

    def get(self, application_id: str) -> dict[str, Any] | None:
        try:
            return self._container.read_item(item=application_id, partition_key=application_id)
        except Exception as e:  # noqa: BLE001 - SDK를 import하지 않고 404만 None으로 바꾼다
            if getattr(e, "status_code", None) == 404:
                return None
            raise


class BlobRawStore:
    def __init__(self, account_url: str, credential: Any) -> None:
        from azure.storage.blob import BlobServiceClient

        self._svc = BlobServiceClient(account_url, credential=credential)

    def get_json(self, container: str, name: str) -> Any | None:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            data = self._svc.get_blob_client(container, name).download_blob().readall()
        except ResourceNotFoundError:
            return None
        return json.loads(data)


class StorageQueueSender:
    def __init__(self, queue_client: Callable[[str], Any]) -> None:
        self._queue_client = queue_client

    def send(self, queue: str, message: dict[str, Any]) -> None:
        # base64 인코딩 없이 JSON 원문을 보낸다. SDK가 XML 본문에 넣을 때 이스케이프한다.
        self._queue_client(queue).send_message(json.dumps(message, ensure_ascii=False))


def _cosmos_container(endpoint: str, database: str, credential: Any) -> Any:
    from azure.cosmos import CosmosClient

    return CosmosClient(endpoint, credential=credential).get_database_client(database).get_container_client(
        APPLICATIONS
    )


def _queue_factory(endpoint: str, credential: Any) -> Callable[[str], Any]:
    from azure.storage.queue import QueueServiceClient

    service = QueueServiceClient(endpoint, credential=credential)
    return service.get_queue_client


def load_pseudonym_key(env: Mapping[str, str], credential: Any) -> bytes:
    if value := env.get("PSEUDONYM_KEY"):
        return value.encode()
    from azure.keyvault.secrets import SecretClient

    return SecretClient(env["KEYVAULT_URI"], credential).get_secret(PSEUDONYM_SECRET).value.encode()


def ManagedIdentityCredential(**kwargs: Any) -> Any:  # noqa: N802 - 테스트에서 바꿔 끼우는 지연 import 래퍼
    from azure.identity import ManagedIdentityCredential as Credential

    return Credential(**kwargs)


REQUIRED_ENV = ("AZURE_CLIENT_ID", "COSMOS_ENDPOINT", "COSMOS_DATABASE", "DATA_BLOB_ENDPOINT", "QUEUE_ENDPOINT")


def build_deps(env: Mapping[str, str] = os.environ) -> ConsoleDeps:
    cfg = {name: env[name] for name in REQUIRED_ENV}
    credential = ManagedIdentityCredential(client_id=cfg["AZURE_CLIENT_ID"])
    return ConsoleDeps(
        store=CosmosApplications(_cosmos_container(cfg["COSMOS_ENDPOINT"], cfg["COSMOS_DATABASE"], credential)),
        raw=BlobRawStore(cfg["DATA_BLOB_ENDPOINT"], credential),
        queue=StorageQueueSender(_queue_factory(cfg["QUEUE_ENDPOINT"], credential)),
        pseudonym_key=load_pseudonym_key(env, credential),
    )
