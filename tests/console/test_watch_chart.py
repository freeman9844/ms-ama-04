from datetime import date, timedelta

import pytest

from console.watch_chart import watch_chart

ORDERS3 = [1.052, 1.057, 1.059, 0.798, 0.514, 0.228, 0.230]
ORDER_GROSS = [615000, 620000, 621200, 157500, 123100, 120000, 160600]
REVENUE3 = [1.095, 1.091, 1.097, 1.061, 0.846, 0.622, 0.478]
REVENUE = [558500, 555300, 564900, 503900, 225800, 222800, 282700]
BALANCE = [1240000, 1240000, 1240000, 1136800, 976100, 532200, -25400]
GRADES = ["Gold"] * 4 + ["Silver"] * 3
STAGES = ["normal"] * 3 + ["watch", "medium", "medium", "high"]
DUES = [
    {"due_on": "2026-12-28", "status": "paid"},
    {"due_on": "2027-01-25", "status": "scheduled"},
    {"due_on": "2027-02-24", "status": "scheduled"},
]


def _dailylook(processed=7):
    start = date(2026, 11, 26)
    weeks, lines = [], []
    for i in range(processed):
        n = i + 1
        weeks.append({"week": n, "row": {
            "start": (start + timedelta(days=7 * i)).isoformat(),
            "orders_vs_expected": ORDERS3[i],
            "order_gross_cents": ORDER_GROSS[i],
            "expected_orders_cents": 584400,
            "revenue_vs_expected": REVENUE3[i],
            "revenue_cents": REVENUE[i],
            "projected_min_balance_cents": BALANCE[i],
            "shortfall_date": "2027-01-14" if BALANCE[i] < 0 else None,
            "mkt_grade": GRADES[i],
            "mkt_cancel_rate": 0.02,
        }})
        lines.append({
            "week": n,
            "stage": STAGES[i],
            "alert_id": f"A-{n}" if n in (5, 7) else None,
            "sources": [
                {"source": "bank", "state": "normal", "signals": []},
                {"source": "settlement", "state": "normal", "signals": []},
                {"source": "market", "state": "signal" if n == 5 else "normal", "signals": []},
            ],
        })
    return weeks, lines[::-1]


def _chart(weeks=None, lines=None, installments=DUES):
    w, ln = _dailylook()
    return watch_chart(weeks if weeks is not None else w, lines if lines is not None else ln, installments)


def _ratio_y(c, v):
    r = c["ratio"]
    return r["base"] - (r["base"] - r["top"]) * v / 1.2


def _point(line, week):
    return next(p for p in line["points"] if p["week"] == week)


def test_slots_and_links():
    c = _chart()
    assert len(c["slots"]) == 13
    assert [s["href"] for s in c["slots"][:7]] == [f"#journal-w{n}" for n in range(1, 8)]
    assert c["slots"][6]["current"] is True
    assert [s["current"] for s in c["slots"]].count(True) == 1
    assert c["slots"][4]["alert_stage"] == "medium"
    assert c["slots"][6]["alert_stage"] == "high"
    assert c["slots"][5]["alert_stage"] is None
    assert [s["stage"] for s in c["slots"][:7]] == STAGES
    assert c["width"] == 960 and c["x0"] == 56 and c["x1"] == 912


def test_unprocessed_slots_are_empty():
    c = _chart()
    for s in c["slots"][7:]:
        assert (s["done"], s["href"], s["stage"]) == (False, None, None)
    for line in c["ratio"]["lines"].values():
        assert all(p["week"] <= 7 for p in line["points"])
    assert all(b["week"] <= 7 for b in c["cash"]["bars"])
    assert all(p["week"] <= 7 for p in c["cash"]["balance"]["points"])
    dues = {d["week"]: d for d in c["dues"]}
    assert (dues[9]["state"], dues[9]["due_on"]) == ("future", "2027-01-25")
    assert dues[13]["state"] == "future"
    assert dues[5]["state"] == "paid"
    assert dues[9]["cx"] == c["slots"][8]["cx"]


def test_orders_single_week_drops_before_three_week():
    c = _chart()
    lines = c["ratio"]["lines"]
    one = _point(lines["orders1"], 4)
    assert one["value"] == pytest.approx(157500 / 584400)
    assert one["y"] > c["ratio"]["threshold_y"]
    assert _point(lines["orders3"], 4)["y"] < c["ratio"]["threshold_y"]
    assert c["ratio"]["threshold_y"] == pytest.approx(_ratio_y(c, 0.75), abs=0.01)
    assert [t["text"] for t in c["ratio"]["ticks"]] == ["0%", "50%", "100%"]


def test_ratio_gaps_and_clipping():
    weeks, lines = _dailylook()
    weeks[1]["row"]["orders_vs_expected"] = None
    weeks[0]["row"]["orders_vs_expected"] = 1.5
    weeks[2]["row"]["expected_orders_cents"] = 0
    c = _chart(weeks, lines)
    o3 = c["ratio"]["lines"]["orders3"]
    assert len(o3["segments"]) == 2
    first = _point(o3, 1)
    assert first["clipped"] is True and first["value"] == 1.5
    assert first["y"] == pytest.approx(c["ratio"]["top"])
    assert _point(o3, 3)["clipped"] is False
    assert 3 not in [p["week"] for p in c["ratio"]["lines"]["orders1"]["points"]]
    assert len(c["ratio"]["lines"]["revenue3"]["segments"]) == 1


def test_cash_axis_signed():
    c = _chart()
    cash = c["cash"]
    low = _point(cash["balance"], 7)
    assert low["negative"] is True and low["y"] > cash["zero_y"]
    assert low["shortfall_date"] == "2027-01-14"
    assert _point(cash["balance"], 6)["negative"] is False
    assert len(cash["bars"]) == 14
    for b in cash["bars"]:
        assert b["h"] >= 0
        assert b["y"] + b["h"] == pytest.approx(cash["zero_y"], abs=0.01)
    assert {t["text_cents"] for t in cash["ticks"]} == {-25400, 0, 1240000}


def test_grade_chip_missing_market_row():
    weeks, lines = _dailylook()
    del weeks[5]["row"]["mkt_grade"]
    del weeks[5]["row"]["mkt_cancel_rate"]
    lines[1]["sources"][2]["state"] = "missing"
    c = _chart(weeks, lines)
    grades = {g["week"]: g for g in c["grades"]}
    assert len(grades) == 7
    assert (grades[6]["grade"], grades[6]["signal"]) == (None, False)
    assert (grades[5]["grade"], grades[5]["signal"]) == ("Silver", True)
    assert grades[4]["signal"] is False


def test_due_marks_only_inside_window():
    assert _chart(installments=[])["dues"] == []
    c = _chart(installments=[*DUES, {"due_on": "2027-06-01", "status": "scheduled"}])
    assert [d["due_on"] for d in c["dues"]] == ["2026-12-28", "2027-01-25", "2027-02-24"]


def test_none_without_journal():
    weeks, _lines = _dailylook()
    assert watch_chart(weeks, [], []) is None
    assert watch_chart([], [], []) is None
