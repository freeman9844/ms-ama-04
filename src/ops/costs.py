"""고정비: Cost Management 실측(리소스 그룹, 최근 완료 7일) 일평균 × 30. 모델 사용료는 실측 토큰으로 따로 계산하므로 뺀다."""
from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

from ops.azcli import OpsError, Runner, rest_post

API = "2023-11-01"
EXCLUDED = frozenset({"Foundry Models"})
COST_COLUMNS = ("totalCost", "Cost", "PreTaxCost")
DAYS = 7
RETRIES = 5
RETRY_WAIT = 20.0  # Probe P6: retry-after 2~20초. az rest는 헤더를 주지 않으므로 상한으로 고정


def period(today: date) -> tuple[date, date]:
    # 데이터가 약 1일 늦으므로 어제도 미완료로 본다.
    end = today - timedelta(days=2)
    return end - timedelta(days=DAYS - 1), end


def _body(start: date, end: date) -> dict:
    return {
        "type": "ActualCost",
        "timeframe": "Custom",
        "timePeriod": {"from": f"{start}T00:00:00Z", "to": f"{end}T23:59:59Z"},
        "dataset": {
            "granularity": "Daily",
            "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
            "grouping": [{"type": "Dimension", "name": "MeterCategory"}],
        },
    }


def _query(az: Runner, url: str, body: dict, sleep: Callable[[float], None]) -> dict:
    for attempt in range(1, RETRIES + 1):
        try:
            return rest_post(az, url, body)
        except OpsError as err:
            text = str(err)
            if attempt == RETRIES or ("429" not in text and "Too Many Requests" not in text):
                raise
            sleep(RETRY_WAIT)
    raise OpsError("unreachable")


def _rows(response: dict) -> tuple[list[dict], str]:
    # 응답 열 이름은 집계 별칭(totalCost)이 아니라 원본 열(Cost·PreTaxCost)로 올 수 있다.
    props = response["properties"]
    columns = props["columns"]
    names = [c["name"] for c in columns]
    cost = next((n for n in COST_COLUMNS if n in names), None) or next(
        (c["name"] for c in columns if c.get("type") == "Number" and c["name"] != "UsageDate"), None)
    if cost is None:
        raise KeyError(f"비용 열 없음({', '.join(names)})")
    return [dict(zip(names, r, strict=True)) for r in props["rows"]], cost


def monthly_fixed_usd(az: Runner, env: dict[str, str], today: date, *, fx_krw_per_usd: float | None = None,
                      sleep: Callable[[float], None]) -> tuple[float, str]:
    start, end = period(today)
    url = (f"https://management.azure.com/subscriptions/{env['AZURE_SUBSCRIPTION_ID']}/resourceGroups/"
           f"{env['AZURE_RESOURCE_GROUP']}/providers/Microsoft.CostManagement/query?api-version={API}")
    try:
        rows, cost = _rows(_query(az, url, _body(start, end), sleep))
        days = {r["UsageDate"] for r in rows}
        kept = [r for r in rows if r.get("MeterCategory") not in EXCLUDED]
        total = sum(float(r[cost]) for r in kept)
    except (KeyError, ValueError, TypeError, IndexError) as err:
        raise OpsError(f"비용 응답 해석 실패({type(err).__name__}: {err})") from err
    if not days:
        raise OpsError(f"비용 데이터 없음({start}~{end})")
    currencies = {r.get("Currency") for r in rows} - {None}
    note = ""
    if currencies - {"USD"}:
        if currencies != {"KRW"} or not fx_krw_per_usd:
            raise OpsError(f"비용 통화 {sorted(currencies)}: --fx-krw-per-usd로 원/달러 환율을 주세요")
        total /= fx_krw_per_usd
        note = f", 환율 {fx_krw_per_usd:g}원/USD"
    monthly = round(total / len(days) * 30, 2)
    return monthly, (f"Cost Management 실측 {start}~{end} 일평균({len(days)}일) × 30, "
                     f"Foundry Models 제외{note}")
