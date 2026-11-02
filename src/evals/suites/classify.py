"""분류 suite: classify_unresolved를 구성별(Decision-1+mini 폴백 / mini만 / gpt-5.4만)로 돌려 정답과 비교한다.

정답 비교 단위는 고유 적요 그룹(파이프라인이 실제로 모델에 묻는 단위)이고, 거래 가중 정확도도 함께 낸다.
"""

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agents.classify import Category, classify
from agents.data_assembly import build_pseudonymizer
from agents.txn_classify import ABSTAIN, Decider, LlmFallback, _groups, classify_unresolved
from evals.datasets import ClassifyApp
from evals.metering import Meter
from evals.metrics import accuracy, macro_f1, per_label, rate
from evals.runner import EvalRunner
from evals.suites import budget_left, cost_of, failure

SUITE = "classify"
THRESHOLDS = (0.5, 0.6, 0.7, 0.8, 0.9)
PRODUCTION_THRESHOLD = 0.7


@dataclass(frozen=True)
class ClassifyConfig:
    name: str
    use_decision: bool
    fallback_model: str


def configs(decision: str, mini: str, full: str) -> list[ClassifyConfig]:
    return [
        ClassifyConfig(f"{decision}+{mini}", True, mini),
        ClassifyConfig(mini, False, mini),
        ClassifyConfig(full, False, full),
    ]


class AbstainDecider:
    """LLM만 쓰는 구성: 모든 항목을 폴백으로 보낸다(모델 호출 없음)."""

    def choose(self, state: dict[str, str]) -> tuple[str, float]:
        return ABSTAIN, 0.0


class RecordingDecider:
    """원시 (선택, 신뢰도)를 남겨 임계값별 정확도·폴백 비율을 사후에 계산한다."""

    def __init__(self, inner: Decider) -> None:
        self._inner = inner
        self.answers: dict[tuple[str, ...], tuple[str, float] | None] = {}

    def choose(self, state: dict[str, str]) -> tuple[str, float]:
        key = (state["business_industry"], state["direction"], state["transaction_type"], state["description"])
        try:
            answer = self._inner.choose(state)
        except Exception:
            self.answers[key] = None
            raise
        self.answers[key] = answer
        return answer


class MemoryCache:
    def __init__(self) -> None:
        self.data: dict[str, dict[str, Any]] = {}

    def get(self, key: str) -> dict[str, Any] | None:
        return self.data.get(key)

    def put(self, key: str, entry: dict[str, Any]) -> None:
        self.data[key] = entry


def _truth_groups(app: ClassifyApp, mask: Callable[[str], str]) -> dict[str, tuple[str, tuple[str, ...]]]:
    """그룹 key → (정답 라벨, 상태 key). 정답 없는 그룹은 뺀다."""
    out: dict[str, tuple[str, tuple[str, ...]]] = {}
    industry = app.case.profile.industry
    for g in _groups(app.transactions, industry, mask):
        labels = Counter(app.truth[t] for t in g.txn_ids if t in app.truth)
        if labels:
            out[g.key] = (labels.most_common(1)[0][0], (industry, g.direction, g.txn_type, g.description))
    return out


def threshold_curve(answers: dict[tuple[str, ...], tuple[str, float] | None], truth: dict[tuple[str, ...], str]) -> list[dict[str, Any]]:
    scored = [(a, truth[k]) for k, a in answers.items() if a is not None and k in truth]
    curve = []
    for t in THRESHOLDS:
        accepted = [(a, y) for a, y in scored if a[0] != ABSTAIN and a[1] >= t]
        curve.append({
            "threshold": t,
            "accepted": len(accepted),
            "accuracy": rate(sum(a[0] == y for a, y in accepted), len(accepted)),
            "fallback_rate": rate(len(scored) - len(accepted), len(scored)),
        })
    return curve


def run(
    apps: list[ClassifyApp],
    cfgs: list[ClassifyConfig],
    *,
    meter: Meter,
    runner: EvalRunner,
    decider: Decider,
    pseudonym_key: bytes,
) -> dict[str, Any]:
    result: dict[str, Any] = {"partial": False, "skipped": 0, "configs": {}, "cases": [], "failures": []}
    for cfg in cfgs:
        recording = RecordingDecider(decider) if cfg.use_decision else None
        cache = MemoryCache()
        truth_all: list[str] = []
        pred_all: list[str | None] = []
        weights: list[int] = []
        state_truth: dict[tuple[str, ...], str] = {}
        sources: Counter[str] = Counter()
        totals: Counter[str] = Counter()
        done = 0
        for app in apps:
            if not budget_left(meter, result):
                continue
            mask = build_pseudonymizer(pseudonym_key, app.case.profile, app.transactions).mask
            fallback = LlmFallback(runner, cfg.fallback_model)
            entities = {**app.case.known_entities, app.case.profile.business_reg_no: "BIZNO"}
            with meter.scope(suite=SUITE, config=cfg.name, case=app.app_id, entities=entities):
                try:
                    out = classify_unresolved(
                        app.transactions,
                        industry=app.case.profile.industry,
                        mask=mask,
                        decider=recording or AbstainDecider(),
                        fallback=fallback,
                        cache=cache,
                        threshold=PRODUCTION_THRESHOLD,
                    )
                except Exception as e:  # noqa: BLE001 - 파이프라인은 예외를 던지지 않지만 평가는 계속한다
                    result["cases"].append({"config": cfg.name, "case": app.app_id, "status": "error"})
                    result["failures"].append(failure("agent_error", SUITE, app.app_id, f"{type(e).__name__}: {e}", cfg.name))
                    continue
            done += 1
            totals.update({k: v for k, v in out.metrics.items() if isinstance(v, int) and not isinstance(v, bool)})
            items = {i["key"]: i for i in out.items}
            correct = 0
            for key, (label, state) in _truth_groups(app, mask).items():
                item = items[key]
                pred = item["label"]
                truth_all.append(label)
                pred_all.append(pred)
                weights.append(item["count"])
                state_truth[state] = label
                sources[item["source"]] += 1
                if pred == label:
                    correct += 1
                elif pred is None:
                    result["failures"].append(failure(
                        "classification_unresolved", SUITE, app.app_id, f"'{item['description']}' 정답 {label}", cfg.name))
                else:
                    result["failures"].append(failure(
                        "classification_wrong", SUITE, app.app_id,
                        f"'{item['description']}' 정답 {label}, 예측 {pred}({item['source']})", cfg.name))
            if out.metrics["fallback_errors"] or out.metrics["decision_errors"]:
                result["failures"].append(failure(
                    "agent_error", SUITE, app.app_id,
                    f"decision 오류 {out.metrics['decision_errors']}건, 폴백 오류 {out.metrics['fallback_errors']}건", cfg.name))
            result["cases"].append({"config": cfg.name, "case": app.app_id, "status": "ok", "metrics": out.metrics,
                                    "groups": len(items), "correct": correct})
        calls = meter.model_calls(suite=SUITE, config=cfg.name)
        cost = cost_of(calls)
        result["configs"][cfg.name] = {
            "apps": done,
            "groups": len(truth_all),
            "macro_f1": macro_f1(truth_all, pred_all),
            "accuracy": accuracy(truth_all, pred_all),
            "txn_accuracy": rate(sum(w for w, t, p in zip(weights, truth_all, pred_all, strict=True) if t == p), sum(weights)),
            "per_label": per_label(truth_all, pred_all),
            "sources": dict(sources),
            "unresolved_rate": rate(sources.get("none", 0), len(truth_all)),
            "cache_hit_rate": rate(totals["cache_hits"], totals["unique_descriptions"]),
            "fallback_rate": rate(totals["fallback_items"], totals["unique_descriptions"] - totals["cache_hits"]),
            "model_calls_per_app": rate(len(calls), done),
            **cost,
            "cost_per_group_usd": round(cost["cost_usd"] / len(truth_all), 6) if truth_all else None,
            "threshold_curve": threshold_curve(recording.answers, state_truth) if recording else None,
        }
    txns = [t for a in apps for t in a.transactions]
    rule = sum(classify(t) not in (Category.UNCLASSIFIED_IN, Category.UNCLASSIFIED_OUT) for t in txns)
    result["total_txns"] = len(txns)
    result["rule_resolved_ratio"] = rate(rule, len(txns))
    return result
