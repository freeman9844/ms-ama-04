import json

from agents.features import ANNUAL_RATE
from console.assessment import assessment_panels
from console.officer_chat import (
    HISTORY_MESSAGES,
    MESSAGE_MAX,
    SETTLEMENT_KEYS,
    SNAPSHOT_MAX_CHARS,
    TRACE_NOTES,
    build_snapshot,
    clean_history,
    validate_question,
)
from console.views import detail_view
from policy.engine import evaluate
from policy.rules import rules_for_version
from shared.contracts import CashFlowFeatures


def _view(make_doc, key, **overrides):
    return detail_view(make_doc(**overrides), None, key, restore=False)


def test_snapshot_keeps_whitelisted_sections(make_doc, key):
    snap = build_snapshot(_view(make_doc, key))
    assert set(snap) == {"application", "cashflow", "policy", "agent_opinion", "officer_decision", "truncated"}
    assert snap["application"] == {"application_id": "app-1", "status": "awaiting_officer",
                                   "requested_amount_krw": 30_000_000, "requested_tenor_months": 12,
                                   "industry": "cafe"}
    cash = snap["cashflow"]
    assert cash["monthly"] == [{"month": "2026-09", "revenue": 40_000_000, "operating_out": 30_000_000,
                                "debt_service": 1_000_000, "owner_draw": 1_000_000}]
    assert cash["features"] == {"dscr": 1.8, "avg_monthly_inflow_krw": 42_000_000}
    assert cash["signals"] == {"owner_draw_ratio": 0.1}
    assert cash["capacity"] == {"new_installment_krw": 0, "months": 1, "short_months": [],
                                "assumed_annual_rate": ANNUAL_RATE}
    assert cash["settlement"] == {"available": False}
    assert snap["policy"]["reason_codes"] == [{"code": "R101", "message": "DSCR 충족", "observed": 1.8,
                                               "threshold": 1.25}]
    assert snap["policy"]["max_amount_krw"] == 20_000_000
    opinion = snap["agent_opinion"]
    assert opinion["recommendation"] == "approve" and opinion["review_passed"] is True
    assert opinion["claims"][1] == {"text": "DSCR 1.8로 정책 기준을 넘는다",
                                    "evidence": {"S:2026-09:index": 1.0, "P:R101:threshold": 1.25}}
    assert opinion["proposed_structure"]["amount_krw"] == 20_000_000
    assert snap["officer_decision"] is None and snap["truncated"] is False


def test_snapshot_capacity_matches_detail_panel(make_doc, key):
    uw = make_doc()["underwriting"] | {"signals": {"new_installment_krw": 9_500_000}}
    view = _view(make_doc, key, underwriting=uw)
    capacity = build_snapshot(view)["cashflow"]["capacity"]
    assert capacity == {"new_installment_krw": 9_500_000, "months": 1, "short_months": ["2026-09"],
                        "assumed_annual_rate": ANNUAL_RATE}
    assert len(capacity["short_months"]) == assessment_panels(view)["capacity"]["short_months"]


def test_snapshot_settlement_subset(make_doc, key):
    pg = {"available": True, "pg_reconciliation_ratio": 0.82, "pg_top_platform_share": 0.9,
          "pg_take_rate_trend_pp": 1.4, "pg_cash_conversion_days": 6.3, "pg_offset_rate_trend_pp": 0.21,
          "pg_advance_share": 0.125, "pg_advance_suspected_count": 2, "unmatched_count": 3, "settlement_count": 40,
          "period": {"from": "2026-04-01", "to": "2026-09-30"},
          "unmatched": [{"platform": "coupang", "amount_krw": 1}], "platforms": [{"platform": "x", "share": 1}]}
    uw = make_doc()["underwriting"] | {"pg_ledger": pg}
    settlement = build_snapshot(_view(make_doc, key, underwriting=uw))["cashflow"]["settlement"]
    assert settlement == {k: pg[k] for k in SETTLEMENT_KEYS}
    assert "unmatched" not in settlement


def test_snapshot_officer_checks_subset(make_doc, key):
    uw = make_doc()["underwriting"] | {"features": make_doc()["underwriting"]["features"]
                                       | {"pg_reconciliation_ratio": 0.82}}
    doc = make_doc(underwriting=uw)
    doc["assessment"] = doc["assessment"] | {
        "officer_checks": [{"text": "정산 대사율이 낮다", "metric_ids": ["M:pg_reconciliation_ratio"], "supported": True}]
    }
    opinion = build_snapshot(detail_view(doc, None, key, restore=False))["agent_opinion"]
    assert opinion["officer_checks"] == [{"text": "정산 대사율이 낮다",
                                         "evidence": {"M:pg_reconciliation_ratio": 0.82}, "supported": True}]


def test_snapshot_officer_checks_empty_for_legacy_assessment(make_doc, key):
    opinion = build_snapshot(_view(make_doc, key))["agent_opinion"]
    assert opinion["officer_checks"] == []


def test_fit_trims_officer_checks_last(make_doc, key):
    doc = make_doc()
    doc["assessment"] = doc["assessment"] | {
        "officer_checks": [{"text": f"확인 {i} " + "가" * 900, "metric_ids": ["M:dscr"], "supported": True}
                           for i in range(40)]
    }
    snap = build_snapshot(detail_view(doc, None, key, restore=False))
    assert len(json.dumps(snap, ensure_ascii=False)) <= SNAPSHOT_MAX_CHARS
    assert snap["truncated"] is True and 0 < len(snap["agent_opinion"]["officer_checks"]) < 40


def test_snapshot_excludes_identifiers(make_doc, key):
    officer = {"decision": "approve", "amount_krw": 15_000_000, "officer_id": "oid-officer",
               "reason": "김대표와 통화함", "conditions": ["빈스카페 매출 확인"], "policy_override": False}
    snap = build_snapshot(_view(make_doc, key, officer_decision=officer))
    text = json.dumps(snap, ensure_ascii=False)
    for secret in ("빈스카페", "김대표", "123-45-67890", "seller-dailylook-01", "oid-officer", "통화함", "급여 이직원"):
        assert secret not in text
    assert snap["officer_decision"] == {"decision": "approve", "amount_krw": 15_000_000}


def test_snapshot_without_policy_or_assessment(make_doc, key):
    snap = build_snapshot(_view(make_doc, key, status="assessing", policy=None, assessment=None))
    assert snap["policy"] is None and snap["agent_opinion"] is None


def test_snapshot_trims_claims_from_end_to_cap(make_doc, key):
    doc = make_doc()
    claims = [{"text": f"주장 {i} " + "가" * 900, "metric_ids": ["M:dscr"], "txn_ids": []} for i in range(40)]
    doc["assessment"] = doc["assessment"] | {"claims": claims}
    snap = build_snapshot(detail_view(doc, None, key, restore=False))
    assert len(json.dumps(snap, ensure_ascii=False)) <= SNAPSHOT_MAX_CHARS
    assert snap["truncated"] is True
    kept = snap["agent_opinion"]["claims"]
    assert 0 < len(kept) < 40 and kept[0]["text"].startswith("주장 0 ")


def test_validate_question():
    assert validate_question("  왜 회부?  ") == "왜 회부?"
    assert validate_question("가" * 500) == "가" * 500
    for bad in ("", "   ", "가" * 501, None, 3, ["q"]):
        assert validate_question(bad) is None


def test_clean_history_keeps_recent_valid_messages():
    raw = [{"role": "user" if i % 2 == 0 else "assistant", "text": f"{i}" + "나" * 3000} for i in range(20)]
    raw += [{"role": "system", "text": "x"}, {"role": "user"}, "bad", {"role": "user", "text": "   "}]
    history = clean_history(raw)
    assert len(history) == HISTORY_MESSAGES
    assert history[0]["text"].startswith("8") and all(len(m["text"]) == MESSAGE_MAX for m in history)
    assert {m["role"] for m in history} == {"user", "assistant"}
    assert clean_history("nope") == [] and clean_history(None) == []


def _decided_doc(make_doc, version="2026.11-v3", **kw):
    """정책 엔진으로 판정한 특성·판정을 담은 신청 문서."""
    base = dict(application_id="app-1", months_of_history=12, avg_monthly_inflow_krw=3_000_000, dscr=1.8,
                cash_buffer_days=30.0, revenue_volatility=0.2, tax_bank_match_ratio=0.95,
                requested_amount_krw=3_000_000, requested_tenor_months=12, industry="cafe", region="서울 성동구",
                business_age_months=38) | kw
    f = CashFlowFeatures(**base)
    doc = make_doc(received_at="2026-11-09T09:00:00+09:00")
    doc["underwriting"] = doc["underwriting"] | {"features": f.model_dump(mode="json")}
    doc["policy"] = evaluate(f, rules_for_version(version)).model_dump(mode="json")
    return doc


def _policy_of(doc, key):
    return build_snapshot(detail_view(doc, None, key, restore=False))["policy"]


def test_snapshot_policy_rules_and_trace(make_doc, key):
    p = _policy_of(_decided_doc(make_doc, business_age_months=11), key)
    assert p["decision"] == "refer"
    assert p["rules"]["version"] == "2026.11-v3" and p["rules"]["product"]["limit_unit_krw"] == 100_000
    assert p["rules"]["product"]["inflow_multiplier"] == 1.5
    assert p["rules"]["refers"][0] == {"code": "R102", "message": "매출 변동성 과다", "field": "revenue_volatility",
                                       "op": "gt", "threshold": 0.5}
    assert p["rules"]["rate_bands"] == [{"band": "A", "min_dscr": 2.0}, {"band": "B", "min_dscr": 1.5},
                                        {"band": "C", "min_dscr": 1.2}, {"band": "D", "min_dscr": 1.0}]
    assert p["trace_note"] is None
    trace = p["trace"]
    assert {c["code"] for c in trace["checks"] if c["result"] == "hit"} == {"R104"}
    assert next(c for c in trace["checks"] if c["code"] == "R102") == {
        "code": "R102", "message": "매출 변동성 과다", "kind": "refer", "field": "revenue_volatility", "op": "gt",
        "observed": 0.2, "threshold": 0.5, "result": "pass"}
    assert trace["band"]["band"] == p["rate_band"] == "B"
    assert trace["band"]["bands"] == p["rules"]["rate_bands"]
    assert trace["limit"]["limit_krw"] == p["max_amount_krw"] == 3_000_000
    assert set(trace) == {"checks", "limit", "band"}


def test_snapshot_trace_for_v1_decision(make_doc, key):
    p = _policy_of(_decided_doc(make_doc, version="2026.11-v1"), key)
    assert p["rules"]["version"] == "2026.11-v1" and p["trace"] is not None and p["trace_note"] is None


def test_snapshot_unknown_version(make_doc, key):
    doc = _decided_doc(make_doc)
    doc["policy"]["rules_version"] = "2026.10-v0"
    p = _policy_of(doc, key)
    assert p["rules"] is None and p["trace"] is None and p["trace_note"] == TRACE_NOTES["unknown_version"]


def test_snapshot_trace_null_when_features_invalid(make_doc, key):
    doc = _decided_doc(make_doc)
    doc["underwriting"]["features"] = {"dscr": 1.8, "avg_monthly_inflow_krw": 42_000_000, "industry": "cafe"}
    p = _policy_of(doc, key)
    assert p["rules"]["version"] == "2026.11-v3"
    assert p["trace"] is None and p["trace_note"] == TRACE_NOTES["invalid_features"]


def test_snapshot_trace_null_on_mismatch(make_doc, key):
    doc = _decided_doc(make_doc)
    doc["policy"]["max_amount_krw"] -= 100_000
    p = _policy_of(doc, key)
    assert p["rules"] is not None and p["trace"] is None and p["trace_note"] == TRACE_NOTES["mismatch"]


def test_snapshot_capacity_states_assumed_rate(make_doc, key):
    assert build_snapshot(_view(make_doc, key))["cashflow"]["capacity"]["assumed_annual_rate"] == ANNUAL_RATE


def test_fit_never_trims_policy(make_doc, key):
    doc = _decided_doc(make_doc)
    full = _policy_of(doc, key)
    claims = [{"text": f"주장 {i} " + "가" * 900, "metric_ids": ["M:dscr"], "txn_ids": []} for i in range(40)]
    doc["assessment"] = doc["assessment"] | {"claims": claims}
    snap = build_snapshot(detail_view(doc, None, key, restore=False))
    assert snap["truncated"] is True and snap["policy"] == full and full["trace"] is not None


def test_snapshot_v1_received_before_in_place_amendment(make_doc, key):
    # v1 규정 파일은 2026-11-04에 버전 이름을 그대로 둔 채 상품 한도가 바뀌었다. 그 전 판정의 규정은 확인할 수 없다.
    doc = _decided_doc(make_doc, version="2026.11-v1")
    doc["received_at"] = "2026-11-03T10:00:00+09:00"
    p = _policy_of(doc, key)
    assert p["rules"] is None and p["trace"] is None and p["trace_note"] == TRACE_NOTES["amended_version"]
    doc["received_at"] = None
    assert _policy_of(doc, key)["trace_note"] == TRACE_NOTES["amended_version"]


def test_snapshot_v3_ignores_v1_amendment(make_doc, key):
    doc = _decided_doc(make_doc)
    doc["received_at"] = "2026-11-03T10:00:00+09:00"
    assert _policy_of(doc, key)["trace"] is not None
