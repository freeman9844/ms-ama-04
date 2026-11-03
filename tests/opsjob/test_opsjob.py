import json
from datetime import UTC, datetime, timedelta

import pytest

from opsjob.run import BUDGET_GUARD_USD, Config, done, message, run

T0 = datetime(2026, 11, 2, 1, 0, tzinfo=UTC)


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t


class FakeQueue:
    def __init__(self):
        self.sent = []

    def send(self, body: str):
        self.sent.append(json.loads(body))


class FakeStore:
    """제출된 신청을 즉시(또는 지정 시점부터) awaiting_officer 상태 문서로 돌려준다."""

    def __init__(self, queue, tokens=0, stuck=(), model="gpt-5.4"):
        self.queue, self.tokens, self.stuck, self.model = queue, tokens, set(stuck), model

    def by_run(self, run_id):
        out = []
        for m in self.queue.sent:
            if m["run_id"] != run_id:
                continue
            reached = m["applicant_id"] not in self.stuck
            timeline = [{"status": "received", "at": m["submitted_at"]}]
            if reached:
                timeline.append({"status": "awaiting_officer", "at": m["submitted_at"]})
            usage = [{"model": self.model, "input_tokens": self.tokens, "output_tokens": 0, "calls": 1}]
            out.append({"application_id": m["application_id"], "applicant_id": m["applicant_id"],
                        "status": "awaiting_officer" if reached else "underwritten", "run_id": run_id,
                        "submitted_at": m["submitted_at"], "timeline": timeline,
                        "business_name": "원문상호", "narrative": "에이전트서술",
                        "assessment": {"recommendation": "approve", "rationale": "판단서술"},
                        "steps": [{"step": "assess", "outcome": "ok", "agent": True, "usage": usage}]})
        return out


def cfg(tmp_path, **kw):
    base = dict(mode="load", run_id="load-1", count=4, interval_s=0, applicants=(), source="load",
                run_ids=(), wait_s=60, out_dir=tmp_path)
    return Config(**{**base, **kw})


def run_with(tmp_path, store_kw=None, **kw):
    q, clock = FakeQueue(), Clock()
    store = FakeStore(q, **(store_kw or {}))
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        clock.t += timedelta(seconds=s)

    summary = run(cfg(tmp_path, **kw), queue=q, store=store, clock=clock, sleep=sleep,
                  prices={"gpt-5.4": {"input_per_1m": 1.0, "output_per_1m": 1.0}})
    return summary, q, sleeps


def test_message_shape():
    m = message("load-1", 3, "load-healthy-01", T0, "load")
    assert m == {"application_id": "load-1-03", "applicant_id": "load-healthy-01", "requested_amount_krw": 10_000_000,
                 "requested_tenor_months": 12, "submitted_at": T0.isoformat(), "source": "load", "run_id": "load-1"}


def test_application_id_matches_entry_pattern():
    import re
    assert re.match(r"^[a-z0-9][a-z0-9-]{2,62}$", message("load-20261102010000", 24, "x", T0, "load")["application_id"])


def test_load_submits_grid_in_order_and_writes_outputs(tmp_path):
    summary, q, _ = run_with(tmp_path)
    assert [m["applicant_id"] for m in q.sent] == ["load-healthy-01", "load-mismatch-01", "load-stress-01", "load-thin-01"]
    assert summary["submitted"] == 4 and summary["reached"] == 4 and summary["unreached"] == []
    docs = json.loads((tmp_path / "docs.json").read_text(encoding="utf-8"))
    assert len(docs["runs"]["load-1"]) == 4
    assert json.loads((tmp_path / "summary.json").read_text(encoding="utf-8")) == summary


def test_wait_reports_unreached(tmp_path):
    summary, _, _ = run_with(tmp_path, store_kw={"stuck": {"load-thin-01"}}, wait_s=120)
    assert summary["reached"] == 3
    assert summary["unreached"] == [{"application_id": "load-1-04", "status": "underwritten"}]


def test_budget_guard_stops_submission(tmp_path):
    # 건당 1M 입력 토큰 × $1 = $1/건 → 24건 예상 $24 > $4
    summary, q, _ = run_with(tmp_path, store_kw={"tokens": 1_000_000}, count=24)
    assert len(q.sent) == 3 and summary["aborted"] is True
    assert summary["guard"]["projected_usd"] == pytest.approx(24.0)
    assert summary["guard"]["limit_usd"] == BUDGET_GUARD_USD


def test_submit_mode_uses_given_applicants(tmp_path):
    summary, q, _ = run_with(tmp_path, mode="submit", run_id="fault-1", source="fault",
                             applicants=("demo-healthy-001", "demo-stress-003"))
    assert [m["applicant_id"] for m in q.sent] == ["demo-healthy-001", "demo-stress-003"]
    assert {m["source"] for m in q.sent} == {"fault"}


def test_collect_mode_submits_nothing(tmp_path):
    summary, q, _ = run_with(tmp_path, mode="collect", run_ids=("load-1",))
    assert q.sent == [] and summary["mode"] == "collect"
    assert json.loads((tmp_path / "docs.json").read_text(encoding="utf-8")) == {"runs": {"load-1": []}}


def test_done_rule():
    assert done({"status": "failed", "timeline": []})
    assert done({"status": "assessed", "timeline": [{"status": "awaiting_officer"}]})
    assert not done({"status": "underwritten", "timeline": [{"status": "received"}]})


def test_config_from_env_validates():
    from opsjob.run import config_from_env

    with pytest.raises(ValueError):
        config_from_env({"OPS_MODE": "load", "OPS_RUN_ID": "bad id"})
    with pytest.raises(ValueError):
        config_from_env({"OPS_MODE": "load", "OPS_RUN_ID": "ok", "OPS_COUNT": "25"})
    c = config_from_env({"OPS_MODE": "submit", "OPS_RUN_ID": "f-1", "OPS_APPLICANTS": "a,b", "OPS_SOURCE": "fault"})
    assert c.applicants == ("a", "b") and c.count == 24
    # 최악 2·대기 + 23·15초 = 3345초 < job-ops replicaTimeout 3600초
    assert c.wait_s == 1500 and c.interval_s == 15

@pytest.mark.parametrize("run_id", ["load_1", "v1.2", "-x", "a" * 61])
def test_config_rejects_run_id_that_breaks_application_id(run_id):
    from opsjob.run import config_from_env

    with pytest.raises(ValueError):
        config_from_env({"OPS_MODE": "load", "OPS_RUN_ID": run_id})


def test_config_accepts_longest_valid_run_id():
    from opsjob.run import config_from_env

    assert config_from_env({"OPS_MODE": "load", "OPS_RUN_ID": "A" * 60}).run_id == "A" * 60


@pytest.mark.parametrize("env", [
    {"OPS_MODE": "load", "OPS_RUN_ID": "load-1", "OPS_SOURCE": "console"},
    {"OPS_MODE": "submit", "OPS_RUN_ID": "f-1", "OPS_SOURCE": ""},
    {"OPS_MODE": "submit", "OPS_RUN_ID": "f-1", "OPS_SOURCE": "fault"},
    {"OPS_MODE": "collect", "OPS_RUN_ID": "c-1"},
])
def test_config_rejects_bad_source_or_missing_mode_inputs(env):
    from opsjob.run import config_from_env

    with pytest.raises(ValueError):
        config_from_env(env)


def test_guard_records_unpriced_calls_without_aborting(tmp_path):
    summary, q, _ = run_with(tmp_path, store_kw={"model": "mystery"}, count=4)
    assert len(q.sent) == 4 and summary["aborted"] is False
    assert summary["guard"]["unpriced_calls"] == 3


def test_docs_keep_only_projected_fields(tmp_path):
    run_with(tmp_path)
    text = (tmp_path / "docs.json").read_text(encoding="utf-8")
    assert "원문상호" not in text and "에이전트서술" not in text and "판단서술" not in text
    doc = json.loads(text)["runs"]["load-1"][0]
    assert "business_name" not in doc and doc["assessment"] == {"recommendation": "approve"}


def test_collect_mode_projects_stored_docs(tmp_path):
    q = FakeQueue()
    q.sent = [message("load-1", i, a, T0, "load") for i, a in enumerate(["load-healthy-01", "load-thin-01"], 1)]
    summary = run(cfg(tmp_path, mode="collect", run_id="c-1", run_ids=("load-1", "load-2")), queue=FakeQueue(),
                  store=FakeStore(q), prices={})
    assert summary["runs"] == {"load-1": 2, "load-2": 0}
    runs = json.loads((tmp_path / "docs.json").read_text(encoding="utf-8"))["runs"]
    assert [d["application_id"] for d in runs["load-1"]] == ["load-1-01", "load-1-02"] and runs["load-2"] == []
    assert "business_name" not in runs["load-1"][0]