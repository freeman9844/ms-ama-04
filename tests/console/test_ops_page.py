import base64
import json
import re
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from console.app import ConsoleDeps, create_app
from console.auth import AUDITOR
from console.ops import default_run, ops_view, sorted_runs

OID = "http://schemas.microsoft.com/identity/claims/objectidentifier"
T0 = datetime(2026, 11, 2, 1, 0, tzinfo=UTC)


def principal(*roles):
    claims = [{"typ": OID, "val": "oid-a"}, {"typ": "name", "val": "감사"}] + [{"typ": "roles", "val": r} for r in roles]
    body = {"auth_typ": "aad", "name_typ": "name", "role_typ": "roles", "claims": claims}
    return {"X-MS-CLIENT-PRINCIPAL": base64.b64encode(json.dumps(body).encode()).decode()}


AUDITOR_H = principal(AUDITOR)


def projected(i, industry, rec, *, age="3년+", arch="healthy"):
    sub = T0 + timedelta(minutes=i)
    return {
        "application_id": f"r-{i:02d}", "applicant_id": f"load-{arch}-{i:02d}", "status": "awaiting_officer",
        "submitted_at": sub.isoformat(), "received_at": sub.isoformat(), "source": "load", "run_id": "load-1",
        "segment": {"industry": industry, "region": "서울", "business_age_band": age},
        "steps": [{"step": "underwrite", "outcome": "ok", "agent": True,
                   "usage": [{"model": "gpt-5.4-mini", "input_tokens": 1000, "output_tokens": 100, "calls": 1}]}],
        "timeline": [{"status": "awaiting_officer", "at": (sub + timedelta(minutes=3)).isoformat()}],
        "flags": [], "policy": {"decision": "eligible", "reason_codes": []},
        "assessment": {"recommendation": rec}, "officer_decision": None,
    }


class FakeOps:
    def __init__(self, runs):
        self.data = runs
        self.asked = []

    def runs(self):
        return list(self.data)

    def by_run(self, run_id):
        self.asked.append(run_id)
        return self.data[run_id]

    def recent(self, limit):
        self.asked.append(("recent", limit))
        return [d for run in self.data.values() for d in run][:limit]


def skewed():
    # 카페 5건 전부 approve, 한식 5건 전부 decline → 업종 격차 100%p(경보). 업력 격차도 100%p지만 R104 설명 축.
    return ([projected(i, "카페", "approve", age="3년+") for i in range(5)]
            + [projected(i + 5, "한식", "decline", age="<1년") for i in range(5)])


def client(ops, key=b"k" * 32):
    deps = ConsoleDeps(store=None, raw=None, queue=None, pseudonym_key=key, ops=ops)
    return TestClient(create_app(deps), follow_redirects=False)


def test_sorted_runs_newest_first_and_default_is_latest_load():
    runs = ["load-20261102010000", "fault-20261102030000", "load-20261102020000", "collect-20261102040000"]
    assert sorted_runs(runs)[0] == "collect-20261102040000"
    assert default_run(runs) == "load-20261102020000"
    assert default_run(["fault-20261102030000"]) == "fault-20261102030000"
    assert default_run([]) is None


def test_ops_page_shows_fairness_table_and_alert_banner():
    ops = FakeOps({"load-20261102010000": skewed()})
    page = client(ops).get("/ops", headers=AUDITOR_H)
    assert page.status_code == 200
    html = page.text
    assert "합성 데이터 기준" in html and "load-20261102010000" in html
    assert 'class="ops-alert"' in html and "업종" in html and "100.0%p" in html
    assert "규칙 R104로 설명" in html  # 업력 축은 경보 대신 설명 표시
    assert "카페" in html and "한식" in html and "100.0%" in html and "0.0%" in html
    assert ops.asked == ["load-20261102010000"]


def test_ops_page_without_alert_has_no_banner():
    docs = [projected(i, "카페" if i % 2 else "한식", "approve") for i in range(10)]
    html = client(FakeOps({"load-20261102010000": docs})).get("/ops", headers=AUDITOR_H).text
    assert 'class="ops-alert"' not in html and "0.0%p" in html


def test_small_groups_marked_insufficient():
    docs = [projected(i, "카페", "approve") for i in range(5)] + [projected(9, "한식", "decline")]
    html = client(FakeOps({"load-20261102010000": docs})).get("/ops", headers=AUDITOR_H).text
    assert re.search(r"<td>한식</td>.*?<td>표본 부족</td>", html, re.S)
    assert not re.search(r"<td>카페</td>(?:(?!</tr>).)*표본 부족", html, re.S)
    assert 'class="ops-alert"' not in html


def test_ops_page_selects_run_and_rejects_unknown():
    ops = FakeOps({"load-20261102010000": skewed(), "fault-20261102030000": skewed()[:2]})
    c = client(ops)
    assert c.get("/ops?run=fault-20261102030000", headers=AUDITOR_H).status_code == 200
    assert ops.asked == ["fault-20261102030000"]
    assert c.get("/ops?run=nope", headers=AUDITOR_H).status_code == 404


def test_ops_page_without_runs_explains_how_to_load():
    ops = FakeOps({})
    html = client(ops).get("/ops", headers=AUDITOR_H).text
    assert "측정 실행이 없습니다" in html and "python -m ops load" in html and "선택한 범위에 신청이 없습니다" in html
    assert ops.asked == [("recent", 100)]


def test_ops_page_offers_recent_first_and_reads_recent_scope():
    ops = FakeOps({"load-20261102010000": skewed()})
    c = client(ops)
    html = c.get("/ops", headers=AUDITOR_H).text
    options = re.findall(r'<option value="([^"]+)"', html)
    assert options == ["recent", "load-20261102010000"] and ops.asked == ["load-20261102010000"]
    html = c.get("/ops?run=recent", headers=AUDITOR_H).text
    assert ops.asked[-1] == ("recent", 100)
    assert '<option value="recent" selected>최근 신청 100건(전체 채널)</option>' in html
    assert "최근 신청 10건(고객 신청·데모·부하 측정 등 전체 채널" in html and "report.md" not in html


def test_ops_page_renders_insight_panels():
    docs = skewed()
    docs[0].update(officer_decision="approve", policy_override=True, status="approved")  # project() 결과 모양
    docs[1].update(officer_decision="decline", status="declined")
    docs[5]["policy"] = {"decision": "refer", "reason_codes": [{"code": "R101", "message": "매출 대사율 미달"}]}
    docs[6].update(flags=["agent_fallback"], status="escalated", sla_escalated=True)
    for d in docs:
        d["steps"][0]["duration_ms"] = 4_200
    html = client(FakeOps({"load-20261102010000": docs})).get("/ops", headers=AUDITOR_H).text
    for text in ("결정 퍼널", "정책 판정", "에이전트 권고", "심사역 결정", "단계별 처리 시간", "시간대별 처리량",
                 "모델별 AI 비용", "단계별 AI 비용", "정책 사유 코드 빈도", "R101", "매출 대사율 미달",
                 "에이전트 대체 경로 사용", "심사역 대기 중", "SLA 초과(누적)"):
        assert text in html, text
    assert "비교 2건 중 일치 1건" in html  # 카페 approve→승인 일치, 카페 approve→거절 불일치
    assert "심사역 승인 1건 중 100.0%" in html
    assert re.search(r'kpi-label">심사역 대기 중</div>\s*<div class="kpi-value">8건', html)


def test_ops_page_requires_role_and_configuration():
    assert client(FakeOps({})).get("/ops").status_code == 401
    assert client(None).get("/ops", headers=AUDITOR_H).status_code == 404


def test_nav_links_to_ops():
    html = client(FakeOps({})).get("/ops", headers=AUDITOR_H).text
    assert '<a href="/ops">운영 지표</a>' in html


def test_ops_view_reads_fairness_gap_override(monkeypatch):
    monkeypatch.setenv("FAIRNESS_GAP_PP", "100")
    fair = ops_view(skewed())["fairness"]
    assert fair["threshold_pp"] == 100.0 and fair["alerts"] == []
    monkeypatch.setenv("FAIRNESS_GAP_PP", "oops")  # 잘못된 값은 기본 20pp로 — 화면은 깨지지 않는다
    fair = ops_view(skewed())["fairness"]
    assert fair["threshold_pp"] == 20.0 and fair["alerts"]


def test_ops_page_and_report_use_timeline_step_terms():
    from console.app import STATUS_LABELS
    from console.ops import AXIS_LABELS, STEP_LABELS
    from ops.opsreport import AXIS_LABELS as REPORT_AXES, STEP_LABELS as REPORT_STEPS

    assert (STEP_LABELS, AXIS_LABELS) == (REPORT_STEPS, REPORT_AXES)
    assert STEP_LABELS["assemble"] == STATUS_LABELS["data_assembled"]  # "데이터 수집"
    assert STEP_LABELS["underwrite"] == STATUS_LABELS["underwritten"]  # "심사 지표 산출"
