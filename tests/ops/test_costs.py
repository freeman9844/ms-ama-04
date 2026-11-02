import json
from datetime import date
from pathlib import Path

import pytest

from ops.azcli import OpsError
from ops.costs import monthly_fixed_usd, period
from ops.kql import dependency_counts

ENV = {"AZURE_SUBSCRIPTION_ID": "sub", "AZURE_RESOURCE_GROUP": "rg"}


def result(rows, currency="USD"):
    return json.dumps({"properties": {
        "columns": [{"name": "totalCost"}, {"name": "UsageDate"}, {"name": "MeterCategory"}, {"name": "Currency"}],
        "rows": [[cost, day, cat, currency] for cost, day, cat in rows]}})


ROWS = [(10.0, 20261025, "Azure Cosmos DB"), (4.0, 20261025, "Foundry Models"),
        (12.0, 20261026, "Azure Cosmos DB"), (2.0, 20261026, "Container Apps")]


def test_period_is_last_seven_completed_days():
    assert period(date(2026, 11, 2)) == (date(2026, 10, 25), date(2026, 10, 31))


def test_cost_excludes_foundry_models_and_retries_429(fake_runner):
    runner = fake_runner(ENV).on("az", "rest", results=[OpsError("명령 실패(1)\n(429) Too Many Requests"), result(ROWS)])
    bodies, sleeps = [], []

    def spy(args, *, stream=False):
        if args[:2] == ["az", "rest"]:
            bodies.append(json.loads(Path(args[args.index("--body") + 1][1:]).read_text(encoding="utf-8")))
        return runner(args, stream=stream)

    usd, source = monthly_fixed_usd(spy, ENV, date(2026, 11, 2), sleep=sleeps.append)
    assert usd == 360.0  # (10 + 12 + 2) / 2일 × 30
    assert sleeps == [20.0]
    assert bodies[0]["timePeriod"] == {"from": "2026-10-25T00:00:00Z", "to": "2026-10-31T23:59:59Z"}
    assert "Foundry Models 제외" in source and "2일" in source


def test_non_usd_requires_fx(fake_runner):
    runner = fake_runner(ENV).on("az", "rest", results=[result([(14000.0, 20261025, "Azure Cosmos DB")], "KRW")])
    with pytest.raises(OpsError, match="--fx-krw-per-usd"):
        monthly_fixed_usd(runner, ENV, date(2026, 11, 2), sleep=lambda s: None)
    usd, source = monthly_fixed_usd(runner, ENV, date(2026, 11, 2), fx_krw_per_usd=1400.0, sleep=lambda s: None)
    assert usd == 300.0 and "1400" in source


def test_non_429_error_is_not_retried(fake_runner):
    runner = fake_runner(ENV).on("az", "rest", results=[OpsError("AuthorizationFailed")])
    with pytest.raises(OpsError, match="AuthorizationFailed"):
        monthly_fixed_usd(runner, ENV, date(2026, 11, 2), sleep=lambda s: pytest.fail("재시도 금지"))


def test_dependency_counts_single_line_query(fake_runner):
    rows = json.dumps([{"app": "fault-1-01", "Target": "ca-policy", "AppRoleName": "orchestrator",
                        "total": "3", "failed": "3"}])
    runner = fake_runner().on("az", "monitor", "log-analytics", "query", results=[rows])
    assert dependency_counts(runner, "law", ["fault-1-01"], sleep=lambda s: None) == {
        "fault-1-01": [{"target": "ca-policy", "role": "orchestrator", "total": 3, "failed": 3}]}
    call = runner.commands("az", "monitor")[0]
    kql = call[call.index("--analytics-query") + 1]
    # az.cmd는 큰따옴표·줄바꿈이 든 인자를 깨뜨린다.
    assert "\n" not in kql and '"' not in kql and "'fault-1-01'" in kql
    # Policy ASGI 내부 스팬(`POST /v1/decisions http send` 등)은 호출 증거가 아니다.
    assert "not(Name endswith ' http send' or Name endswith ' http receive')" in kql


def _named(cost_column, rows):
    return json.dumps({"properties": {
        "columns": [{"name": cost_column, "type": "Number"}, {"name": "UsageDate", "type": "Number"},
                    {"name": "MeterCategory", "type": "String"}, {"name": "Currency", "type": "String"}],
        "rows": [[cost, day, cat, "USD"] for cost, day, cat in rows]}})


@pytest.mark.parametrize("column", ["Cost", "PreTaxCost", "CostUSD"])
def test_cost_column_is_resolved_from_response(fake_runner, column):
    # 응답 열 이름은 별칭(totalCost)이 아니라 원본 열(Cost·PreTaxCost)로 올 수 있다. 그 밖엔 첫 Number 열.
    runner = fake_runner(ENV).on("az", "rest", results=[_named(column, ROWS)])
    usd, _ = monthly_fixed_usd(runner, ENV, date(2026, 11, 2), sleep=lambda s: None)
    assert usd == 360.0


@pytest.mark.parametrize("body", [
    {"properties": {"columns": [{"name": "UsageDate", "type": "Number"}], "rows": [[20261025]]}},
    {"properties": {"columns": [{"name": "Cost"}, {"name": "UsageDate"}], "rows": [["x", 20261025]]}},
    {"properties": {"columns": [{"name": "Cost"}], "rows": [[1.0]]}},
    {"unexpected": True},
])
def test_malformed_response_is_ops_error(fake_runner, body):
    runner = fake_runner(ENV).on("az", "rest", results=[json.dumps(body)])
    with pytest.raises(OpsError, match="비용 응답 해석 실패"):
        monthly_fixed_usd(runner, ENV, date(2026, 11, 2), sleep=lambda s: None)
