"""Summarize one completed underwriting cohort offline; emit aggregates only."""

import argparse
import csv
import json
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import TextIO


FIELDS = ("application_id", "outcome", "hands_on_minutes", "labor_krw_per_hour", "nonlabor_krw")
OUTCOMES = ("funded", "declined", "rejected", "withdrawn", "other_unfunded")
BASES = ("observed", "hypothetical")


def _quantity(text: str, field: str) -> Decimal:
    if not text.strip():
        raise ValueError(f"missing {field}")
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise ValueError(f"{field} must be a number") from None
    if not value.is_finite() or value < 0:
        raise ValueError(f"{field} must be finite and nonnegative")
    if value.adjusted() >= 18 or value.as_tuple().exponent < -6:
        raise ValueError(f"{field} exceeds 18 integer or 6 fractional digits")
    return value


def summarize(stream: TextIO, *, fixed_krw: str, basis: str) -> dict:
    """Return a period summary. Decimal strings are rounded only for display."""
    if basis not in BASES:
        raise ValueError("basis must be observed or hypothetical")
    fixed = _quantity(fixed_krw, "fixed_krw")
    reader = csv.reader(stream, strict=True)
    header = next(reader, [])
    if len(header) != len(FIELDS) or set(header) != set(FIELDS):
        raise ValueError("CSV header must contain exactly once: " + ",".join(FIELDS))
    seen = set()
    funded = 0
    minutes = weighted_minutes = nonlabor = Decimal(0)
    with localcontext() as ctx:
        ctx.prec = 64
        for values in reader:
            if len(values) != len(header):
                raise ValueError(f"line {reader.line_num}: unexpected column count")
            row = dict(zip(header, (item.strip() for item in values)))
            try:
                token = row["application_id"]
                if not token:
                    raise ValueError("missing application_id")
                if token in seen:
                    raise ValueError("duplicate application_id")
                outcome = row["outcome"]
                if outcome not in OUTCOMES:
                    raise ValueError("outcome must be one of: " + ", ".join(OUTCOMES))
                duration = _quantity(row["hands_on_minutes"], "hands_on_minutes")
                rate = _quantity(row["labor_krw_per_hour"], "labor_krw_per_hour")
                nonlabor += _quantity(row["nonlabor_krw"], "nonlabor_krw")
            except ValueError as exc:
                raise ValueError(f"line {reader.line_num}: {exc}") from None
            seen.add(token)
            funded += outcome == "funded"
            minutes += duration
            weighted_minutes += duration * rate
        count = len(seen)
        labor = weighted_minutes / 60
        total = labor + nonlabor + fixed

        def display(value: Decimal, denominator: int = 1) -> str | None:
            if not denominator:
                return None
            return str((value / denominator).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

        return {
            "basis": basis, "applications": count, "funded_loans": funded,
            "hands_on_minutes": display(minutes), "minutes_per_application": display(minutes, count),
            "minutes_per_funded_loan": display(minutes, funded), "labor_krw": display(labor),
            "nonlabor_krw": display(nonlabor), "fixed_krw": display(fixed), "total_krw": display(total),
            "krw_per_application": display(total, count), "krw_per_funded_loan": display(total, funded),
        }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""Example (PowerShell, repository root; example cost is hypothetical):
  python -m src.opsmetrics.pilot cases.csv --basis hypothetical --fixed-krw 1500

UTF-8 CSV header:
  {','.join(FIELDS)}
Use an opaque case token, never a customer identifier. Include one row per
completed application in the period, not just funded loans. Outcomes:
  {', '.join(OUTCOMES)}
Sum officers' active working minutes; exclude queue/waiting time. Supply the
local hourly labor rate (time-weighted if multiple officers) and nonlabor
case spend in KRW. Do not count these costs again in the period allocation.
Use observed only if ALL inputs are locally measured; use hypothetical if any
are assumed or synthetic. This tool cannot verify measurement provenance.
Costs and minutes for all completed cases enter BOTH unit metrics. JSON uses
decimal strings rounded to 2 places; a zero denominator gives null.""",
    )
    parser.add_argument("csv_path", type=Path, help="local minimized CSV for one period")
    parser.add_argument("--basis", required=True, choices=BASES)
    parser.add_argument("--fixed-krw", required=True, help="fixed expense allocated to this cohort, once")
    args = parser.parse_args(argv)
    try:
        if str(args.csv_path).startswith(("\\\\", "//")):
            raise ValueError("use a local CSV, not a network path")
        with args.csv_path.open(encoding="utf-8-sig", newline="") as stream:
            result = summarize(stream, fixed_krw=args.fixed_krw, basis=args.basis)
    except (OSError, ValueError, csv.Error) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
