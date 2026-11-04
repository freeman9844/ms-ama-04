"""콘솔의 Azure 어댑터(관리 ID). Azure SDK는 지연 import한다.

- Cosmos `applications`·`loans`·`alerts`·`consents`·`deletion_proofs`·`grievances`:
  읽기 전용(Built-in Data Reader, 데이터베이스 범위)
- Blob 원천 데이터: 읽기 전용(실명 복원용)
- Storage Queue: applications-start·officer-decisions·monitoring-replay·restructure-decisions·
  consent-commands·grievances·grievance-decisions에 보내기만 한다
  (Functions host.json messageEncoding=none)
"""

import json
import os
from collections.abc import Callable, Mapping
from typing import Any

from console.app import ConsoleDeps
from console.auth import AUDITOR, CREDIT_MANAGER, CREDIT_OFFICER, Principal
from opsmetrics.projection import project

APPLICATIONS = "applications"
LOANS = "loans"
ALERTS = "alerts"
CONSENTS = "consents"
PROOFS = "deletion_proofs"
GRIEVANCES = "grievances"
PSEUDONYM_SECRET = "pseudonym-key"
LIST_QUERY = (
    "SELECT TOP @limit c.id, c.applicant_id, c.status, c.requested_amount_krw, c.updated_at, c.source, "
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


LOANS_QUERY = (
    "SELECT TOP @limit c.id, c.applicant_id, c.industry, c.amount_krw, c.tenor_months, c.updated_at, "
    "c.monitoring.status AS monitoring_status, ARRAY_LENGTH(c.monitoring.alert_ids) AS alerts "
    "FROM c ORDER BY c.updated_at DESC"
)
# 같은 파티션(loan_id)에 연락 기록 문서(contacts-*)도 있어 id 접두사로 경보만 고른다.
ALERTS_QUERY = "SELECT * FROM c WHERE c.replay_id = @replay AND STARTSWITH(c.id, 'ew-')"


class CosmosMonitoring:
    def __init__(self, loans: Any, alerts: Any) -> None:
        self._loans = loans
        self._alerts = alerts

    def list_loans(self, limit: int) -> list[dict[str, Any]]:
        items = self._loans.query_items(
            query=LOANS_QUERY, parameters=[{"name": "@limit", "value": limit}], enable_cross_partition_query=True
        )
        return list(items)

    def get_loan(self, loan_id: str) -> dict[str, Any] | None:
        try:
            return self._loans.read_item(item=loan_id, partition_key=loan_id)
        except Exception as e:  # noqa: BLE001 - SDK를 import하지 않고 404만 None으로 바꾼다
            if getattr(e, "status_code", None) == 404:
                return None
            raise

    def list_alerts(self, loan_id: str, replay_id: str) -> list[dict[str, Any]]:
        items = self._alerts.query_items(
            query=ALERTS_QUERY, parameters=[{"name": "@replay", "value": replay_id}], partition_key=loan_id
        )
        return list(items)


CONSENTS_BY_APPLICANT = "SELECT * FROM c WHERE c.applicant_id = @applicant ORDER BY c.granted_at DESC"
CONSENTS_BY_APPLICATION = "SELECT * FROM c WHERE c.application_id = @application ORDER BY c.granted_at DESC"
# 목록에는 차주가 쓴 이의 내용(text)을 싣지 않는다.
GRIEVANCES_QUERY = (
    "SELECT TOP @limit c.id, c.grievance_id, c.application_id, c.applicant_id, c.status, c.submitted_at, "
    "c.resolved_at FROM c ORDER BY c.submitted_at DESC"
)
GRIEVANCES_BY_APPLICANT = "SELECT * FROM c WHERE c.applicant_id = @applicant ORDER BY c.submitted_at DESC"


def _read_or_none(container: Any, item: str, partition_key: str) -> dict[str, Any] | None:
    try:
        return container.read_item(item=item, partition_key=partition_key)
    except Exception as e:  # noqa: BLE001 - SDK를 import하지 않고 404만 None으로 바꾼다
        if getattr(e, "status_code", None) == 404:
            return None
        raise


class CosmosPrivacy:
    """동의(consents), 삭제 증명(deletion_proofs), 이의제기(grievances) 읽기."""

    def __init__(self, consents: Any, proofs: Any, grievances: Any) -> None:
        self._consents = consents
        self._proofs = proofs
        self._grievances = grievances

    @staticmethod
    def _query(container: Any, query: str, name: str, value: Any) -> list[dict[str, Any]]:
        items = container.query_items(
            query=query, parameters=[{"name": name, "value": value}], enable_cross_partition_query=True
        )
        return list(items)

    def consents_for_applicant(self, applicant_id: str) -> list[dict[str, Any]]:
        return self._query(self._consents, CONSENTS_BY_APPLICANT, "@applicant", applicant_id)

    def consents_for_application(self, application_id: str) -> list[dict[str, Any]]:
        return self._query(self._consents, CONSENTS_BY_APPLICATION, "@application", application_id)

    def get_consent(self, consent_id: str) -> dict[str, Any] | None:
        return _read_or_none(self._consents, consent_id, consent_id)

    def get_proof(self, consent_id: str) -> dict[str, Any] | None:
        return _read_or_none(self._proofs, consent_id, consent_id)

    def list_grievances(self, limit: int) -> list[dict[str, Any]]:
        return self._query(self._grievances, GRIEVANCES_QUERY, "@limit", limit)

    def grievances_for_applicant(self, applicant_id: str) -> list[dict[str, Any]]:
        return self._query(self._grievances, GRIEVANCES_BY_APPLICANT, "@applicant", applicant_id)

    def get_grievance(self, application_id: str, grievance_id: str) -> dict[str, Any] | None:
        return _read_or_none(self._grievances, grievance_id, application_id)


RUNS_QUERY = "SELECT DISTINCT VALUE c.run_id FROM c WHERE IS_STRING(c.run_id)"
RUN_DOCS_QUERY = "SELECT * FROM c WHERE c.run_id = @run"
RECENT_DOCS_QUERY = "SELECT TOP @limit * FROM c WHERE IS_STRING(c.received_at) ORDER BY c.received_at DESC"


class CosmosOps:
    """측정 실행별·최근 신청 투영(applications 읽기 전용). 원문 필드는 project()가 버린다."""

    def __init__(self, applications: Any) -> None:
        self._applications = applications

    def runs(self) -> list[str]:
        return list(self._applications.query_items(query=RUNS_QUERY, enable_cross_partition_query=True))

    def by_run(self, run_id: str) -> list[dict[str, Any]]:
        items = self._applications.query_items(
            query=RUN_DOCS_QUERY, parameters=[{"name": "@run", "value": run_id}], enable_cross_partition_query=True
        )
        return [project(d) for d in items]

    def recent(self, limit: int) -> list[dict[str, Any]]:
        items = self._applications.query_items(
            query=RECENT_DOCS_QUERY, parameters=[{"name": "@limit", "value": limit}], enable_cross_partition_query=True
        )
        return [project(d) for d in items]


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


def _cosmos_database(endpoint: str, database: str, credential: Any) -> Any:
    from azure.cosmos import CosmosClient

    return CosmosClient(endpoint, credential=credential).get_database_client(database)


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
CONSOLE_ROLES = (CREDIT_OFFICER, CREDIT_MANAGER, AUDITOR)


def demo_principal(env: Mapping[str, str]) -> Principal | None:
    """CONSOLE_AUTH_MODE=demo(Easy Auth 끔)이면 CONSOLE_DEMO_ROLE(기본 CreditOfficer)의 고정 주체."""
    mode = env.get("CONSOLE_AUTH_MODE", "easyauth")
    if mode == "easyauth":
        return None
    if mode != "demo":
        raise ValueError(f"CONSOLE_AUTH_MODE는 easyauth 또는 demo입니다: {mode}")
    role = env.get("CONSOLE_DEMO_ROLE", CREDIT_OFFICER)
    if role not in CONSOLE_ROLES:
        raise ValueError(f"CONSOLE_DEMO_ROLE은 {', '.join(CONSOLE_ROLES)} 중 하나입니다: {role}")
    return Principal(user_id="demo-operator", name="데모 사용자", roles=frozenset({role}))


def build_deps(env: Mapping[str, str] = os.environ) -> ConsoleDeps:
    cfg = {name: env[name] for name in REQUIRED_ENV}
    demo = demo_principal(env)
    credential = ManagedIdentityCredential(client_id=cfg["AZURE_CLIENT_ID"])
    db = _cosmos_database(cfg["COSMOS_ENDPOINT"], cfg["COSMOS_DATABASE"], credential)
    return ConsoleDeps(
        store=CosmosApplications(db.get_container_client(APPLICATIONS)),
        monitoring=CosmosMonitoring(db.get_container_client(LOANS), db.get_container_client(ALERTS)),
        privacy=CosmosPrivacy(*(db.get_container_client(name) for name in (CONSENTS, PROOFS, GRIEVANCES))),
        raw=BlobRawStore(cfg["DATA_BLOB_ENDPOINT"], credential),
        queue=StorageQueueSender(_queue_factory(cfg["QUEUE_ENDPOINT"], credential)),
        pseudonym_key=load_pseudonym_key(env, credential),
        demo_principal=demo,
        ops=CosmosOps(db.get_container_client(APPLICATIONS)),
    )
