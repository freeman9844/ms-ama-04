import json

from console.assessment import assessment_panels
from console.officer_chat import (
    HISTORY_MESSAGES,
    MESSAGE_MAX,
    SNAPSHOT_MAX_CHARS,
    build_snapshot,
    clean_history,
    validate_question,
)
from console.views import detail_view


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
    assert cash["capacity"] == {"new_installment_krw": 0, "months": 1, "short_months": []}
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
    assert capacity == {"new_installment_krw": 9_500_000, "months": 1, "short_months": ["2026-09"]}
    assert len(capacity["short_months"]) == assessment_panels(view)["capacity"]["short_months"]


def test_snapshot_settlement_subset(make_doc, key):
    pg = {"available": True, "pg_reconciliation_ratio": 0.82, "pg_top_platform_share": 0.9,
          "pg_take_rate_trend_pp": 1.4, "unmatched_count": 3, "settlement_count": 40,
          "period": {"from": "2026-04-01", "to": "2026-09-30"},
          "unmatched": [{"platform": "coupang", "amount_krw": 1}], "platforms": [{"platform": "x", "share": 1}]}
    uw = make_doc()["underwriting"] | {"pg_ledger": pg}
    settlement = build_snapshot(_view(make_doc, key, underwriting=uw))["cashflow"]["settlement"]
    assert settlement == {k: pg[k] for k in ("available", "pg_reconciliation_ratio", "pg_top_platform_share",
                                             "pg_take_rate_trend_pp", "unmatched_count", "settlement_count", "period")}


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
