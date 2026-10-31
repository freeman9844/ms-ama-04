"""Azure·HTTP 어댑터와 앱 설정에서 Deps 조립. Azure SDK는 지연 import한다."""

import json
import os
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import httpx

from agents.data_assembly import DEFAULT_MODEL, SourceError
from agents.runtime import FoundryAgents
from journey.activities import Deps
from journey.models import KST
from shared.contracts import PolicyDecision

FOUNDRY_SCOPE = "https://ai.azure.com/.default"
PSEUDONYM_SECRET = "pseudonym-key"
SOURCE_PATHS = {
    "profile": "/mydata/profile",
    "transactions": "/mydata/transactions",
    "tax": "/tax/filings",
    "pg": "/pg/settlements",
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


class ServiceBusPublisher:
    def __init__(self, fqdn: str, credential: Any) -> None:
        from azure.servicebus import ServiceBusClient

        self._client = ServiceBusClient(fqdn, credential)

    def publish(self, topic: str, message: dict[str, Any], message_id: str) -> None:
        from azure.servicebus import ServiceBusMessage

        with self._client.get_topic_sender(topic) as sender:
            sender.send_messages(
                ServiceBusMessage(
                    json.dumps(message, ensure_ascii=False),
                    message_id=message_id,
                    content_type="application/json",
                    subject=message.get("type"),
                )
            )


def load_pseudonym_key(env: Mapping[str, str], credential: Any) -> bytes:
    if value := env.get("PSEUDONYM_KEY"):
        return value.encode()
    from azure.keyvault.secrets import SecretClient

    return SecretClient(env["KEYVAULT_URI"], credential).get_secret(PSEUDONYM_SECRET).value.encode()


def build_deps(env: Mapping[str, str] = os.environ) -> Deps:
    from azure.identity import ManagedIdentityCredential

    credential = ManagedIdentityCredential(client_id=env["AZURE_CLIENT_ID"])
    agents = None
    if endpoint := env.get("AI_PROJECT_ENDPOINT"):
        agents = FoundryAgents(endpoint, lambda: credential.get_token(FOUNDRY_SCOPE).token)
    fqdn = env.get("SERVICEBUS_FQDN")
    return Deps(
        store=CosmosStore(env["COSMOS_ENDPOINT"], env["COSMOS_DATABASE"], credential),
        raw=BlobRawStore(env["DATA_BLOB_ENDPOINT"], credential),
        mock=HttpMockApi(env["MOCKAPI_URL"]),
        policy=HttpPolicyApi(env["POLICY_URL"]),
        events=ServiceBusPublisher(fqdn, credential) if fqdn else None,
        pseudonym_key=load_pseudonym_key(env, credential),
        clock=lambda: datetime.now(KST),
        agents=agents,
        model=env.get("DATA_ASSEMBLY_MODEL", DEFAULT_MODEL),
    )
