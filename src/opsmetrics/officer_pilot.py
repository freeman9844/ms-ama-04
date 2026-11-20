"""Compare paired shadow reviews of identical evidence with and without an AI memo."""

import argparse
import csv
import json
from collections import Counter
from decimal import Decimal
from io import StringIO
from pathlib import Path
from typing import TextIO

from . import pilot

FIELDS = ("case_id", "evidence_version", "policy_result", "reviewer", "arm", "hands_on_minutes",
          "labor_krw_per_hour", "nonlabor_krw", "requests", "errors", "decision", "override_reason")
ARMS = ("no_memo", "memo")
DECISIONS = ("eligible", "refer", "ineligible")
REASONS = ("verified_evidence", "missing_evidence", "policy_exception", "other")


def compare(stream: TextIO, *, basis: str) -> dict:
    reader = csv.DictReader(stream, strict=True)
    if sorted(reader.fieldnames or ()) != sorted(FIELDS):
        raise ValueError("CSV header must contain exactly: " + ",".join(FIELDS))
    cases: dict[str, dict[str, dict]] = {}
    for raw in reader:
        if None in raw or None in raw.values():
            raise ValueError(f"line {reader.line_num}: unexpected column count")
        row = {k: v.strip() for k, v in raw.items()}
        where = f"line {reader.line_num}"
        if not all(row[k] for k in ("case_id", "reviewer", "evidence_version")):
            raise ValueError(f"{where}: missing case, reviewer or evidence version")
        arm = row["arm"]
        if arm not in ARMS or any(row[k] not in DECISIONS for k in ("policy_result", "decision")):
            raise ValueError(f"{where}: invalid arm or decision")
        override = row["decision"] != row["policy_result"]
        if row["override_reason"] not in (REASONS if override else ("",)):
            raise ValueError(f"{where}: override reason inconsistent with decision")
        for field in ("requests", "errors"):
            if not row[field].isascii() or not row[field].isdecimal():
                raise ValueError(f"{where}: {field} must be a nonnegative integer")
        for field in ("hands_on_minutes", "labor_krw_per_hour", "nonlabor_krw"):
            pilot._quantity(row[field], field)
        pair = cases.setdefault(row["case_id"], {})
        if arm in pair:
            raise ValueError(f"{where}: duplicate arm")
        if pair:
            other = next(iter(pair.values()))
            if (other["reviewer"] == row["reviewer"] or other["evidence_version"] != row["evidence_version"]
                    or other["policy_result"] != row["policy_result"]):
                raise ValueError(f"{where}: require distinct reviewers, identical evidence and policy")
        pair[arm] = row

    complete = [p for p in cases.values() if len(p) == 2]
    result = {"basis": basis, "cases": len(cases), "complete_pairs": len(complete),
              "incomplete_cases": len(cases) - len(complete)}
    assignment = {arm: Counter(pair[arm]["reviewer"] for pair in complete) for arm in ARMS}
    a, b = (assignment[arm] for arm in ARMS)
    if not complete:
        result["reviewer_assignment"] = "unobserved"
    elif not (a.keys() & b.keys()):
        result["reviewer_assignment"] = "confounded"
    else:
        result["reviewer_assignment"] = "balanced" if a == b else "imbalanced"
    for arm in ARMS:
        costs = StringIO()
        writer = csv.writer(costs)
        writer.writerow(pilot.FIELDS)
        requests = errors = 0
        reasons = Counter()
        for pair in complete:
            row = pair[arm]
            writer.writerow([row["case_id"], "other_unfunded", row["hands_on_minutes"],
                             row["labor_krw_per_hour"], row["nonlabor_krw"]])
            requests += int(row["requests"])
            errors += int(row["errors"])
            if row["override_reason"]:
                reasons[row["override_reason"]] += 1
        costs.seek(0)
        metrics = pilot.summarize(costs, fixed_krw="0", basis=basis)
        result[arm] = {k: metrics[k] for k in ("minutes_per_application", "krw_per_application")}
        result[arm].update(requests=requests if complete else None, errors=errors if complete else None,
                           overrides=sum(reasons.values()) if complete else None, override_reasons=dict(reasons) if complete else None)
    result["memo_minus_no_memo"] = {
        k: str(Decimal(result["memo"][k]) - Decimal(result["no_memo"][k])) if complete else None
        for k in ("minutes_per_application", "krw_per_application")
    }
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--basis", required=True, choices=pilot.BASES)
    args = parser.parse_args()
    try:
        if str(args.csv_path).startswith(("\\\\", "//")):
            raise ValueError("use a local CSV")
        with args.csv_path.open(encoding="utf-8-sig", newline="") as stream:
            print(json.dumps(compare(stream, basis=args.basis), indent=2))
    except (ValueError, OSError, csv.Error) as exc:
        parser.error(str(exc))
