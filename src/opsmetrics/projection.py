"""신청 문서 → 지표 계산용 투영. 허용 필드만 남긴다(원문 PII·에이전트 서술 제외)."""

import re
from typing import Any

_LOAD_ID = re.compile(r"^load-(healthy|mismatch|stress|thin)-\d+$")
_STEP_KEYS = ("step", "outcome", "started_at", "ended_at", "duration_ms", "agent", "usage", "detail")


def archetype_of(applicant_id: str | None) -> str | None:
    m = _LOAD_ID.match(applicant_id or "")
    return m.group(1) if m else None


def project(doc: dict[str, Any]) -> dict[str, Any]:
    policy, assessment, officer = doc.get("policy"), doc.get("assessment"), doc.get("officer_decision")
    return {
        "application_id": doc.get("application_id"),
        "applicant_id": doc.get("applicant_id"),
        "status": doc.get("status"),
        "submitted_at": doc.get("submitted_at"),
        "received_at": doc.get("received_at"),
        "source": doc.get("source"),
        "run_id": doc.get("run_id"),
        "segment": doc.get("segment"),
        "steps": [{k: s[k] for k in _STEP_KEYS if k in s} for s in doc.get("steps") or []],
        "timeline": [{"status": t.get("status"), "at": t.get("at")} for t in doc.get("timeline") or []],
        "flags": list(doc.get("flags") or []),
        "policy": {"decision": policy.get("decision"), "reason_codes": list(policy.get("reason_codes") or [])}
        if isinstance(policy, dict) else None,
        "assessment": {"recommendation": assessment.get("recommendation")} if isinstance(assessment, dict) else None,
        "officer_decision": officer.get("decision") if isinstance(officer, dict) else None,
    }
