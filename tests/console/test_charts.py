import math

from console import charts


def test_donut_skips_zero_negative_and_missing_and_chains_offsets():
    d = charts.donut([("a", 3, "blue"), ("b", 1, "red"), ("c", 0, "green"), ("d", -2, "amber"), ("e", None, "grey")])
    assert d["total"] == 4 and d["circumference"] == 289.03
    assert [(a["label"], a["dash"], a["gap"], a["offset"], a["pct"]) for a in d["arcs"]] == [
        ("a", 216.77, 72.26, 0.0, 75.0),
        ("b", 72.26, 216.77, -216.77, 25.0),
    ]


def test_donut_all_zero_has_no_arcs():
    assert charts.donut([("a", 0, "blue"), ("b", None, "red")])["arcs"] == []
    assert charts.donut([])["total"] == 0


def test_hbars_scale_to_max_and_keep_original_value():
    h = charts.hbars([("x", 10, "blue"), ("y", 5, "red"), ("z", -1, "grey")])
    assert h["height"] == 84
    assert [(b["w"], b["y"], b["ty"], b["vx"], b["value"]) for b in h["bars"]] == [
        (208.0, 6.0, 18.0, 318.0, 10), (104.0, 34.0, 46.0, 214.0, 5), (0.0, 62.0, 74.0, 110.0, -1)]


def test_hbars_zero_max_and_empty():
    assert charts.hbars([("a", 0, "blue")])["bars"][0]["w"] == 0.0
    assert charts.hbars([]) == {"width": 360, "height": 0, "bars": []}


def test_columns_stack_parts_bottom_up_and_include_refs_in_axis():
    c = charts.columns(
        [("1월", [[("rev", 100, "blue", "t1")]]),
         ("2월", [[("rev", 50, "blue", "t2")],
                  [("op", 30, "amber", "t3"), ("ds", -5, "red", "t4"), ("dr", 20, "purple", "t5")]])],
        refs=[("기준", 120, "neutral")],
    )
    assert (c["x0"], c["x1"], c["base"], c["label_y"], c["vmax"]) == (56, 592, 214, 230, 120.0)
    assert [t["text"] for t in c["ticks"]] == ["0", "60", "120"]
    g1, g2 = c["groups"]
    assert (g1["cx"], g1["bars"][0]["x"], g1["bars"][0]["w"]) == (190.0, 105.58, 168.84)
    assert (g1["bars"][0]["parts"][0]["y"], g1["bars"][0]["parts"][0]["h"]) == (44.0, 170.0)
    expense = g2["bars"][1]
    assert [(p["part"], p["y"], p["h"]) for p in expense["parts"]] == [
        ("op", 163.0, 51.0), ("ds", 163.0, 0.0), ("dr", 129.0, 34.0)]
    assert expense["parts"][1]["value"] == -5 and expense["parts"][1]["tip"] == "t4"
    assert expense["top"] == 129.0
    assert c["refs"] == [{"label": "기준", "value": 120, "color": "neutral", "y": 10.0, "text": "120"}]


def test_columns_fixed_vmax_clamps_height_and_empty_has_unit_axis():
    c = charts.columns([("a", [[("v", 150, "blue", "")]])], vmax=100)
    assert c["groups"][0]["bars"][0]["parts"][0]["h"] == 204.0 and c["vmax"] == 100.0
    empty = charts.columns([])
    assert empty["groups"] == [] and empty["vmax"] == 1.0 and empty["refs"] == []


def test_columns_skip_non_numeric_refs_and_use_tick_format():
    c = charts.columns([("a", [[("v", 4, "blue", "")]])], refs=[("x", None, "red")], tick_fmt=lambda v: f"{v:.0f}%")
    assert c["refs"] == [] and c["ticks"][-1]["text"] == "4%"


def test_line_breaks_at_missing_values():
    ln = charts.line([10, None, 30, 20])
    assert ln["segments"] == ["M 123.0 146.0", "M 391.0 10.0 L 525.0 78.0"]
    assert [p["i"] for p in ln["points"]] == [0, 2, 3] and ln["x"] == 592 and ln["vmax"] == 30.0
    assert charts.line([])["segments"] == [] and charts.line([None])["points"] == []


def test_gauge_clamps_and_handles_missing():
    assert charts.gauge(15, 20)["pct"] == 75.0
    assert charts.gauge(-1, 20)["frac"] == 0.0 and charts.gauge(50, 20)["frac"] == 1.0
    assert charts.gauge(None, 20)["frac"] is None and charts.gauge(5, 0)["frac"] is None
    assert charts.gauge(5, None)["d"] == charts.GAUGE_ARC


def test_marker_bar_needs_two_non_negative_numbers():
    assert charts.marker_bar(1.8, 1.25) == {"fill": 80.0, "mark": 55.56}
    assert charts.marker_bar(0, 0) == {"fill": 0.0, "mark": 0.0}
    assert charts.marker_bar(None, 1) is None and charts.marker_bar(-1, 1) is None
    assert charts.marker_bar(True, 1) is None and charts.marker_bar(1, math.inf) is None


def test_stacked_percent_parts_and_empty_rows():
    rows = charts.stacked([("a", [("ok", 3, "green"), ("degraded", 1, "amber"), ("failed", 0, "red")]),
                           ("b", [("ok", 0, "green")])])
    assert [(p["part"], p["x"], p["w"]) for p in rows[0]["parts"]] == [("ok", 0.0, 75.0), ("degraded", 75.0, 25.0)]
    assert rows[1]["empty"] and rows[1]["parts"] == []


def test_rounding_never_returns_negative_zero():
    assert str(charts._r(-0.001)) == "0.0"
    assert charts.is_num(1) and not charts.is_num(True) and not charts.is_num(math.nan) and not charts.is_num("1")
