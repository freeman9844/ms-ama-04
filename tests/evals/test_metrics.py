from evals.metrics import binary, macro_f1, per_label, percentile


def test_binary_counts_and_rates():
    out = binary([True, True, False, False, True], [True, False, True, False, True])
    assert (out["tp"], out["fp"], out["fn"], out["tn"]) == (2, 1, 1, 1)
    assert out["precision"] == round(2 / 3, 4) and out["recall"] == round(2 / 3, 4)


def test_binary_without_positive_predictions_has_no_precision():
    assert binary([True, False], [False, False])["precision"] is None


def test_unresolved_prediction_counts_against_recall_only():
    labels = per_label(["rent", "rent", "payroll"], ["rent", None, "payroll"])
    assert labels["rent"] == {"support": 2, "precision": 1.0, "recall": 0.5, "f1": round(2 / 3, 4)}
    assert macro_f1(["rent", "rent", "payroll"], ["rent", None, "payroll"]) == round((0.6667 + 1) / 2, 4)


def test_nearest_rank_percentile():
    assert percentile([5, 1, 4, 2, 3], 50) == 3
    assert percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95) == 10
    assert percentile([], 50) is None
