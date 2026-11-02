"""평가 지표 순수 함수. 정의가 없는 값(분모 0)은 None으로 돌려 리포트에서 "측정 불가"로 표시한다."""

import math
from collections.abc import Sequence
from typing import Any


def rate(num: int | float, den: int | float) -> float | None:
    return round(num / den, 4) if den else None


def accuracy(truth: Sequence[str], pred: Sequence[str | None]) -> float | None:
    return rate(sum(t == p for t, p in zip(truth, pred, strict=True)), len(truth))


def per_label(truth: Sequence[str], pred: Sequence[str | None]) -> dict[str, dict[str, float | int | None]]:
    """정답에 나온 라벨마다 precision·recall·F1·support. 예측 None(미분류)은 어느 라벨에도 속하지 않는다."""
    out: dict[str, dict[str, float | int | None]] = {}
    for label in sorted(set(truth)):
        tp = sum(t == label and p == label for t, p in zip(truth, pred, strict=True))
        fp = sum(t != label and p == label for t, p in zip(truth, pred, strict=True))
        fn = sum(t == label and p != label for t, p in zip(truth, pred, strict=True))
        p, r = rate(tp, tp + fp) or 0.0, rate(tp, tp + fn) or 0.0
        out[label] = {"precision": p, "recall": r, "f1": round(2 * p * r / (p + r), 4) if p + r else 0.0, "support": tp + fn}
    return out


def macro_f1(truth: Sequence[str], pred: Sequence[str | None]) -> float | None:
    labels = per_label(truth, pred)
    return round(sum(v["f1"] for v in labels.values()) / len(labels), 4) if labels else None  # type: ignore[misc]


def precision_recall(tp: int, fp: int, fn: int) -> dict[str, float | None]:
    return {"precision": rate(tp, tp + fp), "recall": rate(tp, tp + fn)}


def binary(truth: Sequence[bool], pred: Sequence[bool]) -> dict[str, Any]:
    pairs = list(zip(truth, pred, strict=True))
    tp, fp = sum(t and p for t, p in pairs), sum(p and not t for t, p in pairs)
    fn, tn = sum(t and not p for t, p in pairs), sum(not t and not p for t, p in pairs)
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, **precision_recall(tp, fp, fn)}


def percentile(values: Sequence[float], q: float) -> float | None:
    """nearest-rank 백분위수."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q / 100 * len(ordered)) - 1)]


def distribution(values: Sequence[float]) -> dict[str, float | int | None]:
    return {
        "n": len(values),
        "min": min(values) if values else None,
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "max": max(values) if values else None,
    }
