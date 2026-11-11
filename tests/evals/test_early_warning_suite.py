"""조기경보 suite: 원인 경로 재생, 원인 정확도, ew_wrong_cause(가짜 백엔드)."""

from evals import datasets
from evals.__main__ import load_prices
from evals.backend import Models, fake_backend
from evals.fake import FakeAzure
from evals.metering import Meter
from evals.runner import EvalRunner
from evals.suites import early_warning
from shared.contracts import AlertSeverity

KEY = b"k" * 32


def _run(paths):
    meter = Meter(load_prices(), 5.0)
    models = Models.from_env({})
    backend = fake_backend(meter, models)
    try:
        out = early_warning.run(paths, [models.mini], meter=meter, runner=EvalRunner(backend.agents, meter),
                                pseudonym_key=KEY)
    finally:
        backend.close()
    return out, models.mini


def test_fake_run_scores_causes():
    out, model = _run(datasets.ew_paths())
    m = out["configs"][model]
    assert m["cause_accuracy"] == 1.0 and m["last_cause_accuracy"] == 1.0
    assert out["detector_only"]["cause_accuracy"] == 1.0
    assert not [f for f in out["failures"] if f["type"] in ("ew_missed", "ew_false_alarm", "ew_wrong_cause")]
    # 리드 타임은 라벨 연체일(default_on)이 있는 경로만 센다(시나리오 이름이 아니라).
    assert len(m["lead_days"]) == len([p for p in datasets.ew_paths() if p.flow.default_on])
    rows = {r["case"]: r for r in out["cases"]}
    assert all(rows[p.path_id]["first_alert_cause"] == p.flow.cause.value
               for p in datasets.ew_paths() if p.positive)
    assert all(rows[p.path_id]["in_window"] for p in datasets.ew_paths() if p.positive)


def test_escalation_weeks():
    weeks = [{"week": 3, "severity": "high"}, {"week": 5, "severity": "medium"}, {"week": 7, "severity": "high"}, {"week": 9, "severity": "high"}]
    assert early_warning.escalation_weeks(weeks) == {"stage1_week": 3, "stage2_week": 9}


def test_escalation_weeks_ignores_non_alert_weeks():
    weeks = [{"week": 2, "severity": None}, {"week": 4, "severity": "high"}, {"week": 6, "severity": None},
             {"week": 8, "severity": "high"}]
    assert early_warning.escalation_weeks(weeks) == {"stage1_week": 4, "stage2_week": 8}
    assert early_warning.escalation_weeks([{"week": 1, "severity": "medium"}]) == {"stage1_week": None, "stage2_week": None}


def test_fake_run_reports_stage_counts(monkeypatch):
    # 가짜 백엔드는 medium만 올리므로, 모든 경보를 high로 올려 경로별 단계 도달을 잰다.
    real = early_warning.investigate

    def high(ctx, **kw):
        result = real(ctx, **kw)
        if result.alert is not None:
            result.alert = result.alert.model_copy(update={"severity": AlertSeverity("high")})
        return result

    monkeypatch.setattr(early_warning, "investigate", high)
    out, model = _run(datasets.ew_paths())
    m = out["configs"][model]
    assert {"stage1_paths", "stage2_paths"} <= set(m)
    rows = [r for r in out["cases"] if r["status"] == "ok"]
    alerted = [r for r in rows if r["agent_alerted"]]
    assert m["stage1_paths"] == len(alerted) > 0
    assert all(r["escalation"]["stage1_week"] == r["first_alert_week"] for r in alerted)
    assert m["stage2_paths"] == sum(r["escalation"]["stage2_week"] is not None for r in rows)
    assert all(r["escalation"] == {"stage1_week": None, "stage2_week": None} for r in rows if not r["agent_alerted"])


def test_wrong_cause_is_reported(monkeypatch):
    # 두 번째 후보를 고르는 가짜. 첫 경보 주차에 후보가 하나뿐인 경로에서는 경보를 올리지 않으므로,
    # 정산 보류(3주차) 뒤 차입(5주차)이 겹쳐 후보가 둘이 되는 ev-ehealh로 잰다.
    monkeypatch.setattr(FakeAzure, "ew_cause", lambda self, candidates: candidates[1] if len(candidates) > 1 else None)
    paths = [p for p in datasets.ew_paths() if p.path_id == "ev-ehealh"]
    out, model = _run(paths)
    assert [f["type"] for f in out["failures"] if f["type"] == "ew_wrong_cause"] == ["ew_wrong_cause"]
    assert out["configs"][model]["cause_accuracy"] == 0.0
    assert out["detector_only"]["cause_accuracy"] == 1.0  # 탐지기 후보 자체는 정답을 가리킨다
