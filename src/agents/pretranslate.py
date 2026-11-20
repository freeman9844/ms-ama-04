"""영문 화면 사전 번역의 공유 단위(오케스트레이터 변경 피드 처리기와 콘솔이 함께 쓴다).

- 어떤 문서의 어떤 경로가 번역 대상인지(ALLOWLIST)와 그 문구를 뽑는 규칙(extract).
- 문구를 가명처리할 원천(프로필·거래)을 문서에서 찾아 읽는 규칙(load_case_raw).
- 번역 문서의 키·모양(translation_id·translation_doc)과 토큰 보존 검사(tokens_kept), 배치 분할(batches).
`console`·`journey`는 가져오지 않는다. 두 이미지(콘솔·Functions)에 함께 실린다.
"""

import hashlib
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol

from shared.contracts import ApplicantProfile, Transaction
from shared.opinions import archived_reply, published_opinion
from shared.pseudonymize import tokens

SOURCES = ("applications", "loans", "alerts", "grievances")
BATCH_ITEMS = 25
BATCH_CHARS = 8000

# 컨테이너별 번역 대상 경로. 점으로 키를 따라가고 `[]`는 리스트의 모든 항목이다.
# 콘솔 템플릿의 |mt·|mt_ref 대상을 저장 문서 경로로 되짚은 것이다. 정책 사유 메시지(reason_codes)는
# 카탈로그(locales/en/data.json)에 있어 뺐고, 시스템 오류 문자열(analyst_error 등)과 코드가 만든 문구도 뺐다.
ALLOWLIST: dict[str, tuple[str, ...]] = {
    "applications": (
        "assessment.summary",  # detail: a.summary
        "assessment.claims[].text",  # detail: 근거 표(c.text)
        "assessment.officer_checks[].text",  # detail: 에이전트 확인 사항(i.text)
        "assessment.counterfactuals[].explanation",  # detail: cf.explanation
        "officer_decision.reason",  # detail: d.officer_decision.reason
    ),
    "loans": (
        "repayment.plan_change.reason",  # loan: pc.reason(플랜 변경 결정 사유)
    ),
    "alerts": (
        # 경보 문서
        "summary",  # loan: a.summary
        "check_action",  # 경보의 확인할 조치
        "signals[].text",  # loan: s.text
        "collections.reason",  # loan: collections_block(c.reason, 옛 문서)
        "restructure.note",  # loan: restructure_block(rs.note)
        "restructure.reason",  # loan: restructure_block(rs.reason, 심사역 결정 사유)
        # 미납 추심 건 문서
        "recent_alert.summary",  # loan: c.recent_alert.summary
        "recent_alert.check_action",
        "proposal.reason",  # loan: p.reason
        "proposal.hardship.note",  # loan: p.hardship.note
        "proposal.evidence.recent_alert.summary",  # loan: ev.recent_alert.summary
        "proposal.evidence.recent_alert.check_action",  # loan: ev.recent_alert.check_action
        "proposal.text",  # loan: 고객에게 보낼 문구(p.text|mt_ref)
        "proposal.checks[].detail",  # loan: k.detail(정책 확인 결과)
        "proposal.decision_reason",  # loan: p.decision_reason(심사역 결정 사유)
        "proposal.sent_text",  # loan: 수정해 보낸 문구
        "proposal.contact.text",  # loan: 발송 결과 문구
        # 연락 기록 문서(contacts-*)
        "contacts[].text",  # borrower: 받은 안내(n.text|mt_ref)
    ),
    "grievances": (
        "text",  # grievance·borrower: g.text
        "record.officer_decision.reason",  # grievance: od.reason(접수 시점 원결정)
        "record.assessment.counterfactuals[].explanation",
        "decision.reason",  # grievance: dd.reason
        "reply.title",  # grievance·borrower: g.reply.title
        "reply.body",  # grievance·borrower: g.reply.body
    ),
}
_OWNER_KEY = {
    "applications": "application_id",
    "grievances": "application_id",
    "loans": "loan_id",
    "alerts": "loan_id",
}


class RawStore(Protocol):
    def get_json(self, container: str, name: str) -> Any: ...


def _has_korean(text: Any) -> bool:
    return isinstance(text, str) and any("가" <= ch <= "힣" for ch in text)


def _walk(node: Any, parts: Sequence[str]) -> Iterator[str]:
    if not parts:
        if isinstance(node, str):
            yield node
        return
    head, rest = parts[0], parts[1:]
    each = head.endswith("[]")
    child = node.get(head.removesuffix("[]")) if isinstance(node, Mapping) else None
    if not each:
        yield from _walk(child, rest)
    elif isinstance(child, list):
        for item in child:
            yield from _walk(item, rest)


def extract(source: str, doc: Mapping[str, Any]) -> list[str]:
    """허용 경로의 한국어 문자열을 처음 나온 순서로, 정확히 같은 문자열은 한 번만 돌려준다."""
    found: dict[str, None] = {}
    hide_reply = source == "grievances" and archived_reply(doc)
    if source == "applications":
        doc = {**doc, "assessment": published_opinion(doc.get("assessment"), doc.get("requested_amount_krw"))}
    elif source == "grievances":
        record = doc.get("record") or {}
        doc = {**doc, "record": {**record, "assessment": published_opinion(record.get("assessment"))}}
    for path in ALLOWLIST[source]:
        if hide_reply and path.startswith("reply."):
            continue
        for text in _walk(doc, path.split(".")):
            if _has_korean(text):
                found.setdefault(text, None)
    return list(found)


def owner_of(source: str, doc: Mapping[str, Any]) -> str | None:
    return doc.get(_OWNER_KEY[source]) or None


def raw_container(consent_id: str) -> str:
    # journey.activities.raw_container·console.views.raw_container와 같아야 한다(테스트로 고정).
    return f"consent-{consent_id}"


RawScope = tuple[tuple[str, str], ...]


def translation_scope(source: str, doc: Mapping[str, Any]) -> RawScope:
    if source == "applications":
        cid = (doc.get("consent") or {}).get("consent_id")
        return ((cid, "profile.json"), (cid, "transactions.json")) if cid else ()
    underwriting, monitoring = doc.get("underwriting_consent_id"), (doc.get("monitoring") or {}).get("consent_id")
    return ((underwriting, "profile.json"), (monitoring, "flow.json")) if underwriting and monitoring else ()


def active_scope(scope: RawScope, consent: Callable[[str], Mapping[str, Any] | None]) -> bool:
    # delete_consent_data marks the registry record before attempting Blob deletion.
    for cid in dict.fromkeys(cid for cid, _ in scope):
        record = consent(cid)
        if not record or record.get("status") != "granted" or record.get("deletion_proof_id") or record.get("deletion_complete"):
            return False
    return bool(scope)


def _validated(profile: Any, txns: Any) -> tuple[ApplicantProfile, list[Transaction]] | None:
    if profile is None or txns is None:
        return None
    return ApplicantProfile.model_validate(profile), [Transaction.model_validate(t) for t in txns]


def _application_raw(doc: Mapping[str, Any], raw: RawStore) -> tuple[ApplicantProfile, list[Transaction]] | None:
    consent_id = (doc.get("consent") or {}).get("consent_id")
    if not consent_id:
        return None
    container = raw_container(consent_id)
    return _validated(raw.get_json(container, "profile.json"), raw.get_json(container, "transactions.json"))


def _loan_raw(doc: Mapping[str, Any], raw: RawStore) -> tuple[ApplicantProfile, list[Transaction]] | None:
    # 오케스트레이터 사후 가명처리기와 같은 입력: 심사 동의의 프로필 + 모니터링 동의의 사후 흐름.
    underwriting, monitoring = doc.get("underwriting_consent_id"), (doc.get("monitoring") or {}).get("consent_id")
    if not underwriting or not monitoring:
        return None
    return _validated(
        raw.get_json(raw_container(underwriting), "profile.json"), raw.get_json(raw_container(monitoring), "flow.json")
    )


def load_case_raw(
    source: str, doc: Mapping[str, Any], raw: RawStore, read: Callable[[str, str], dict | None]
) -> tuple[ApplicantProfile, list[Transaction]] | None:
    """문서를 가명처리할 원천(프로필, 거래). 원천이 없으면 None, 저장소 예외는 그대로 올린다."""
    if source == "applications":
        return _application_raw(doc, raw)
    if source == "loans":
        return _loan_raw(doc, raw)
    if source in ("grievances", "alerts"):
        parent_source = "applications" if source == "grievances" else "loans"
        owner = owner_of(source, doc)
        parent = read(parent_source, owner) if owner else None
        if parent is None:
            return None
        return load_case_raw(parent_source, parent, raw, read)
    raise ValueError(f"알 수 없는 소스: {source}")


def translation_id(masked: str) -> str:
    return hashlib.sha256(masked.encode("utf-8")).hexdigest()


def tokens_kept(masked: str, en: str) -> bool:
    """영어 결과가 가명 토큰을 하나도 빼거나 바꾸거나 더하지 않았는지."""
    return tokens(masked) == tokens(en)


def batches(texts: Sequence[str]) -> Iterator[list[str]]:
    batch: list[str] = []
    chars = 0
    for t in texts:
        if batch and (len(batch) >= BATCH_ITEMS or chars + len(t) > BATCH_CHARS):
            yield batch
            batch, chars = [], 0
        batch.append(t)
        chars += len(t)
    if batch:
        yield batch


def translation_doc(owner: str, source: str, masked: str, en: str, model: str, at: datetime) -> dict[str, Any]:
    """translations 컨테이너 문서(pk /owner_id). 원문은 저장하지 않는다."""
    return {
        "id": translation_id(masked),
        "owner_id": owner,
        "source": source,
        "en": en,
        "model": model,
        "translated_at": at.isoformat(),
    }
