import hashlib
import importlib
import json
import sys
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from mockapis.demo import DEMO_APPLICANTS, LOAD_ARCHETYPE, build_demo_cases, build_load_cases, load_secret, resolve_as_of
from mockapis.synth import INJECTION_TEXT, Archetype
from shared.segments import LOAD_GRID, SELLER_INDUSTRIES
from shared.sellers import DEMO_SELLERS, seller_label, seller_name

DIGESTS = {
    "seller-dailylook-01": "9ba75badfa58b748",
    "seller-mangwon-02": "a4b919c799e74916",
    "seller-glowlab-03": "356477d6aa21d76c",
    "seller-knitroom-04": "16f78f197d30271b",
    "seller-candle-05": "ad4ecfd4e1fd5361",
}


def _digest(case) -> str:
    body = {
        "profile": case.profile.model_dump(mode="json"),
        "txns": [t.model_dump(mode="json") for t in case.transactions],
        "tax": [t.model_dump(mode="json") for t in case.tax_filings],
        "pg": [p.model_dump(mode="json") for p in case.pg_settlements],
    }
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def test_build_demo_cases():
    cases = build_demo_cases(date(2026, 10, 1))
    assert {k: v.archetype for k, v in cases.items()} == {
        "seller-dailylook-01": Archetype.HEALTHY_SEASONAL,
        "seller-mangwon-02": Archetype.TAX_MISMATCH,
        "seller-glowlab-03": Archetype.STRESSED,
        "seller-knitroom-04": Archetype.THIN_FILE,
        "seller-candle-05": Archetype.HEALTHY_SEASONAL,
    }
    injected = [cid for cid, c in cases.items() if any(t.description == INJECTION_TEXT for t in c.transactions)]
    assert injected == ["seller-candle-05"]


def test_demo_applicants_follow_seller_catalog():
    assert [a for a, *_ in DEMO_APPLICANTS] == [s.applicant_id for s in DEMO_SELLERS]
    assert seller_label("seller-dailylook-01") == "성수데일리룩 · 스마트스토어·쿠팡·자사몰 · 패션의류"
    assert seller_label("load-healthy-01") == "load-healthy-01"


def test_load_secret_requires_32_chars():
    with pytest.raises(RuntimeError):
        load_secret({})
    with pytest.raises(RuntimeError):
        load_secret({"MOCK_CONSENT_SECRET": "short"})
    assert load_secret({"MOCK_CONSENT_SECRET": "x" * 32}) == b"x" * 32


def test_resolve_as_of_env_override():
    assert resolve_as_of({"MOCK_AS_OF": "2026-10-01"}) == date(2026, 10, 1)


def test_resolve_as_of_uses_kst_date():
    utc_evening = datetime(2026, 9, 30, 16, 0, tzinfo=timezone.utc)  # KST 10-01 01:00
    assert resolve_as_of({}, now=utc_evening) == date(2026, 10, 1)


def test_main_builds_app(monkeypatch):
    monkeypatch.setenv("MOCK_CONSENT_SECRET", "x" * 32)
    monkeypatch.setenv("MOCK_AS_OF", "2026-10-01")
    sys.modules.pop("mockapis.main", None)
    module = importlib.import_module("mockapis.main")
    assert TestClient(module.app).get("/healthz").status_code == 200


def test_postloan_flows_cast_the_healthy_seller_as_the_stacking_hero():
    from mockapis.demo import build_postloan_flows
    from mockapis.postloan import FlowScenario

    as_of = date(2026, 11, 2)
    flows = build_postloan_flows(build_demo_cases(as_of), as_of)
    assert {k: f.scenario for k, f in flows.items()} == {
        "seller-dailylook-01": FlowScenario.STACKING,
        "seller-mangwon-02": FlowScenario.STABLE,
        "seller-glowlab-03": FlowScenario.STABLE,
        "seller-knitroom-04": FlowScenario.STABLE,
        "seller-candle-05": FlowScenario.STABLE,
    }
    assert all(f.start == as_of for f in flows.values())



def test_main_serves_flow_for_load_applicant(monkeypatch):
    """부하 신청을 심사역이 승인해도 사후 재생이 no_flow로 끊기지 않는다."""
    monkeypatch.setenv("MOCK_CONSENT_SECRET", "x" * 32)
    monkeypatch.setenv("MOCK_AS_OF", "2026-10-01")
    sys.modules.pop("mockapis.main", None)
    client = TestClient(importlib.import_module("mockapis.main").app)
    r = client.post("/consents", json={"applicant_id": "load-healthy-12", "scopes": ["account"],
                                       "purpose": "loan-application", "ttl_days": 90})
    token = r.json()["token"]
    flow = client.get("/mydata/flow", headers={"Authorization": f"Bearer {token}"})
    assert flow.status_code == 200 and flow.json()


def test_demo_cases_unchanged():
    cases = build_demo_cases(date(2026, 10, 1))
    assert {k: _digest(v) for k, v in cases.items()} == DIGESTS


def test_load_cases_follow_grid():
    cases = build_load_cases(date(2026, 10, 1))
    assert set(cases) == {a.applicant_id for a in LOAD_GRID}
    for a in LOAD_GRID:
        p = cases[a.applicant_id].profile
        assert (p.industry, p.region, p.business_age_months) == (a.industry, a.region, a.business_age_months)
        assert cases[a.applicant_id].archetype is LOAD_ARCHETYPE[a.archetype]
    names = {cases[a.applicant_id].profile.business_name for a in LOAD_GRID}
    assert not names & {c.profile.business_name for c in build_demo_cases(date(2026, 10, 1)).values()}


def test_load_cases_are_deterministic():
    a, b = build_load_cases(date(2026, 10, 1)), build_load_cases(date(2026, 10, 1))
    assert all(_digest(a[k]) == _digest(b[k]) for k in a)


def test_every_applicant_is_an_online_seller():
    as_of = date(2026, 10, 1)
    cases = {**build_demo_cases(as_of), **build_load_cases(as_of)}
    assert {c.profile.industry for c in cases.values()} <= set(SELLER_INDUSTRIES)

def test_seller_name_matches_generated_business_names():
    as_of = date(2026, 10, 1)
    cases = {**build_demo_cases(as_of), **build_load_cases(as_of)}
    assert {k: seller_name(k) for k in cases} == {k: c.persona.business_name for k, c in cases.items()}
    assert seller_name("someone-else") is None
