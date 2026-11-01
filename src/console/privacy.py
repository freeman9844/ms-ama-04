"""콘솔의 동의·삭제 증명·이의제기 화면 데이터(P5b). 표시 라벨과 저장소 인터페이스."""

from typing import Any, Protocol

from agents.notices import DECISION_LABELS, REASON_LABELS, SCOPE_LABELS

CONSENT_STATUS_LABELS = {"granted": "유효", "expired": "만료", "revoked": "철회", "void": "무효"}
GRIEVANCE_STATUS_LABELS = {
    "pending_officer": "처리 대기",
    "resolved_upheld": "원결정 유지",
    "resolved_rereviewed": "재심사 완료",
}
OUTCOME_LABELS = {"uphold": "원결정 유지", "rereview": "재심사"}
NOTICE_LABELS = {"renewal": "갱신 안내", "deletion": "삭제 완료 안내"}
ITEM_LABELS = {
    "raw_container": "원천 데이터(Blob)",
    "consent_registry": "동의 레지스트리",
    "conversation": "Foundry 대화",
    "container": "코드 실행 컨테이너",
}
# 콘솔이 보내는 데모 명령. 철회(revoke)는 차주 시뮬레이터에서만 보낸다.
DEMO_ACTIONS = {"expire_now": "지금 만료(데모)", "remind_now": "갱신 안내(데모)"}

LABEL_FILTERS = {
    "consent_status": CONSENT_STATUS_LABELS,
    "grievance_status": GRIEVANCE_STATUS_LABELS,
    "outcome_label": OUTCOME_LABELS,
    "notice_label": NOTICE_LABELS,
    "item_label": ITEM_LABELS,
    "scope_label": SCOPE_LABELS,
    "reason_label": REASON_LABELS,
    "decision_label": DECISION_LABELS,
}


class PrivacyStore(Protocol):
    def consents_for_applicant(self, applicant_id: str) -> list[dict[str, Any]]: ...

    def consents_for_application(self, application_id: str) -> list[dict[str, Any]]: ...

    def get_consent(self, consent_id: str) -> dict[str, Any] | None: ...

    def get_proof(self, consent_id: str) -> dict[str, Any] | None: ...

    def list_grievances(self, limit: int) -> list[dict[str, Any]]: ...

    def grievances_for_applicant(self, applicant_id: str) -> list[dict[str, Any]]: ...

    def get_grievance(self, application_id: str, grievance_id: str) -> dict[str, Any] | None: ...
