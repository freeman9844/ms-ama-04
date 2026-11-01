"""Azure·HTTP 어댑터와 앱 설정에서 Deps 조립. Azure SDK는 지연 import한다."""

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

import httpx

from agents.data_assembly import DEFAULT_MODEL, SourceError
from agents.runtime import FoundryAgents
from agents.txn_classify import DecisionClient
from journey.activities import (
    ANALYST_MODEL,
    CLASSIFIER_MODEL,
    COLLECTIONS_MODEL,
    EW_MODEL,
    GRIEVANCE_MODEL,
    NOTICE_MODEL,
    REVIEWER_MODEL,
    Deps,
    StoreCache,
)
from journey.models import KST
from shared.contracts import PolicyDecision

FOUNDRY_SCOPE = "https://ai.azure.com/.default"
COGNITIVE_SCOPE = "https://cognitiveservices.azure.com/.default"
PSEUDONYM_SECRET = "pseudonym-key"
SOURCE_PATHS = {
    "profile": "/mydata/profile",
    "transactions": "/mydata/transactions",
    "tax": "/tax/filings",
    "pg": "/pg/settlements",
    "flow": "/mydata/flow",
}


def _http(base_url: str, client: httpx.Client | None, timeout: float) -> httpx.Client:
    return client or httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)


class HttpMockApi:
    def __init__(self, base_url: str = "", *, client: httpx.Client | None = None, timeout: float = 30.0) -> None:
        self._client = _http(base_url, client, timeout)

    def create_consent(self, applicant_id: str, scopes: list[str], purpose: str, ttl_days: int) -> dict[str, Any]:
        r = self._client.post(
            "/consents",
            json={"applicant_id": applicant_id, "scopes": scopes, "purpose": purpose, "ttl_days": ttl_days},
        )
        r.raise_for_status()
        return r.json()

    def revoke(self, consent_id: str) -> str:
        """Mock 레지스트리는 인메모리라 재시작 뒤에는 404(absent)일 수 있다."""
        r = self._client.post(f"/consents/{consent_id}/revoke")
        if r.status_code == 404:
            return "absent"
        r.raise_for_status()
        return "revoked"

    def fetch(self, source: str, token: str) -> Any:
        try:
            r = self._client.get(SOURCE_PATHS[source], headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError as e:
            raise SourceError(None, type(e).__name__) from e
        if r.status_code >= 400:
            raise SourceError(r.status_code, r.text[:200])
        return r.json()


class PolicyUnavailable(RuntimeError):
    pass


class HttpPolicyApi:
    def __init__(self, base_url: str = "", *, client: httpx.Client | None = None, timeout: float = 30.0) -> None:
        self._client = _http(base_url, client, timeout)

    def decide(self, features: dict[str, Any]) -> dict[str, Any]:
        try:
            r = self._client.post("/v1/decisions", json=features)
        except httpx.HTTPError as e:
            raise PolicyUnavailable(type(e).__name__) from e
        if r.status_code != 200:
            raise PolicyUnavailable(f"HTTP {r.status_code} {r.text[:200]}")
        return PolicyDecision.model_validate(r.json()).model_dump(mode="json")


class CosmosStore:
    def __init__(self, endpoint: str, database: str, credential: Any) -> None:
        from azure.cosmos import CosmosClient

        self._db = CosmosClient(endpoint, credential=credential).get_database_client(database)

    def upsert(self, container: str, document: dict[str, Any]) -> None:
        self._db.get_container_client(container).upsert_item(document)

    def read(self, container: str, item_id: str, partition_key: str) -> dict[str, Any] | None:
        from azure.cosmos.exceptions import CosmosResourceNotFoundError

        try:
            return self._db.get_container_client(container).read_item(item_id, partition_key=partition_key)
        except CosmosResourceNotFoundError:
            return None

    def replace(self, container: str, document: dict[str, Any], etag: str | None) -> bool:
        from azure.core import MatchConditions
        from azure.cosmos.exceptions import CosmosAccessConditionFailedError, CosmosResourceNotFoundError

        client = self._db.get_container_client(container)
        if etag is None:
            client.upsert_item(document)
            return True
        try:
            client.replace_item(document["id"], document, etag=etag, match_condition=MatchConditions.IfNotModified)
        except (CosmosAccessConditionFailedError, CosmosResourceNotFoundError):
            return False
        return True


class BlobRawStore:
    def __init__(self, account_url: str, credential: Any) -> None:
        from azure.storage.blob import BlobServiceClient

        self._svc = BlobServiceClient(account_url, credential=credential)

    def put_json(self, container: str, name: str, value: Any) -> None:
        from azure.core.exceptions import ResourceExistsError

        client = self._svc.get_container_client(container)
        try:
            client.create_container()
        except ResourceExistsError:
            pass
        client.upload_blob(name, json.dumps(value, ensure_ascii=False).encode(), overwrite=True)

    def get_json(self, container: str, name: str) -> Any | None:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            data = self._svc.get_blob_client(container, name).download_blob().readall()
        except ResourceNotFoundError:
            return None
        return json.loads(data)

    def manifest(self, container: str) -> list[dict[str, Any]] | None:
        from azure.core.exceptions import ResourceNotFoundError

        client = self._svc.get_container_client(container)
        try:
            names = sorted(b.name for b in client.list_blobs())
        except ResourceNotFoundError:
            return None
        out = []
        for name in names:
            data = client.download_blob(name).readall()
            out.append({"name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        return out

    def delete_container(self, container: str) -> bool:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            self._svc.delete_container(container)
        except ResourceNotFoundError:
            return False
        return True


class StorageQueueSender:
    """함수 앱 호스트 저장소 큐. host.json이 messageEncoding=none이라 JSON 원문을 보낸다."""

    def __init__(self, account_url: str, credential: Any) -> None:
        from azure.storage.queue import QueueServiceClient

        self._svc = QueueServiceClient(account_url, credential=credential)

    def send(self, queue: str, message: dict[str, Any]) -> None:
        self._svc.get_queue_client(queue).send_message(json.dumps(message, ensure_ascii=False))


class ServiceBusPublisher:
    def __init__(self, fqdn: str, credential: Any) -> None:
        from azure.servicebus import ServiceBusClient

        self._client = ServiceBusClient(fqdn, credential)

    def publish(
        self, topic: str, message: dict[str, Any], message_id: str, session_id: str | None = None
    ) -> None:
        from azure.servicebus import ServiceBusMessage

        with self._client.get_topic_sender(topic) as sender:
            sender.send_messages(
                ServiceBusMessage(
                    json.dumps(message, ensure_ascii=False),
                    message_id=message_id,
                    content_type="application/json",
                    subject=message.get("type"),
                    session_id=session_id,
                )
            )


def load_pseudonym_key(env: Mapping[str, str], credential: Any) -> bytes:
    if value := env.get("PSEUDONYM_KEY"):
        return value.encode()
    from azure.keyvault.secrets import SecretClient

    return SecretClient(env["KEYVAULT_URI"], credential).get_secret(PSEUDONYM_SECRET).value.encode()


def agent_settings(env: Mapping[str, str], token: Callable[[str], str]) -> dict[str, Any]:
    """Foundry 에이전트·Decision-1·모델 설정. 엔드포인트가 없으면 해당 기능은 꺼진다."""
    settings: dict[str, Any] = {}
    if endpoint := env.get("AI_PROJECT_ENDPOINT"):
        settings["agents"] = FoundryAgents(endpoint, lambda: token(FOUNDRY_SCOPE))
        settings["model"] = env.get("DATA_ASSEMBLY_MODEL", DEFAULT_MODEL)
        settings["analyst_model"] = env.get("ANALYST_MODEL", ANALYST_MODEL)
        settings["reviewer_model"] = env.get("REVIEWER_MODEL", REVIEWER_MODEL)
        settings["classifier_model"] = env.get("CLASSIFIER_MODEL", CLASSIFIER_MODEL)
        settings["ew_model"] = env.get("EW_MODEL", EW_MODEL)
        settings["collections_model"] = env.get("COLLECTIONS_MODEL", COLLECTIONS_MODEL)
        settings["notice_model"] = env.get("NOTICE_MODEL", NOTICE_MODEL)
        settings["grievance_model"] = env.get("GRIEVANCE_MODEL", GRIEVANCE_MODEL)
    if (account := env.get("AI_ACCOUNT_ENDPOINT")) and (deployment := env.get("DECISION_DEPLOYMENT")):
        settings["decider"] = DecisionClient(account, deployment, lambda: token(COGNITIVE_SCOPE))
    return settings


def build_deps(env: Mapping[str, str] = os.environ) -> Deps:
    from azure.identity import ManagedIdentityCredential

    credential = ManagedIdentityCredential(client_id=env["AZURE_CLIENT_ID"])
    fqdn = env.get("SERVICEBUS_FQDN")
    store = CosmosStore(env["COSMOS_ENDPOINT"], env["COSMOS_DATABASE"], credential)
    return Deps(
        store=store,
        raw=BlobRawStore(env["DATA_BLOB_ENDPOINT"], credential),
        mock=HttpMockApi(env["MOCKAPI_URL"]),
        policy=HttpPolicyApi(env["POLICY_URL"]),
        events=ServiceBusPublisher(fqdn, credential) if fqdn else None,
        pseudonym_key=load_pseudonym_key(env, credential),
        clock=lambda: datetime.now(KST),
        cache=StoreCache(store),
        # 동의 수명주기 예약에 꼭 필요하다(없으면 시작하지 않는다).
        queue=StorageQueueSender(env["QUEUE_ENDPOINT"], credential),
        **agent_settings(env, lambda scope: credential.get_token(scope).token),
    )
