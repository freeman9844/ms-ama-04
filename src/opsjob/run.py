"""운영 지표 Job 본체. 큐에 신청을 넣고, 심사역 대기열 도달을 기다린 뒤, 투영 문서를 남긴다.

평가 이미지에서 돈다(ops·journey 패키지 없음). 출력은 evals.transport.emit으로 로그에 싣는다.
"""

import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from opsmetrics.cost import ai_cost
from opsmetrics.projection import project
from shared.segments import LOAD_GRID

# 부하 격자 밖 신청자(submit 모드)의 신청 금액. 격자 신청자는 격자 금액을 쓴다.
DEFAULT_AMOUNT_KRW = 1_700_000
_LOAD_AMOUNTS = {a.applicant_id: a.requested_amount_krw for a in LOAD_GRID}
TENOR_MONTHS = 6
MAX_COUNT = len(LOAD_GRID)
GUARD_SAMPLE = 3
BUDGET_GUARD_USD = 8.0
POLL_S = 15
SOURCES = ("load", "fault")
_RUN_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
# journey.models.APPLICATION_ID_PATTERN. 어긋난 메시지는 진입점이 경고만 남기고 버린다.
_APPLICATION_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,62}$")


class Queue(Protocol):
    def send(self, body: str) -> None: ...


class Store(Protocol):
    def by_run(self, run_id: str) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class Config:
    mode: str
    run_id: str
    count: int
    interval_s: float
    applicants: tuple[str, ...]
    source: str
    run_ids: tuple[str, ...]
    wait_s: float
    out_dir: Path


def _csv(value: str | None) -> tuple[str, ...]:
    return tuple(v.strip() for v in (value or "").split(",") if v.strip())


def config_from_env(env: Mapping[str, str], out_dir: Path = Path("/tmp/ops")) -> Config:
    mode = env.get("OPS_MODE", "")
    if mode not in ("load", "submit", "collect"):
        raise ValueError(f"OPS_MODE: {mode}")
    run_id = env.get("OPS_RUN_ID", "")
    if not _RUN_ID.match(run_id):
        raise ValueError("OPS_RUN_ID")
    count = int(env.get("OPS_COUNT", MAX_COUNT))
    if not 1 <= count <= MAX_COUNT:
        raise ValueError(f"OPS_COUNT 1~{MAX_COUNT}")
    run_ids = _csv(env.get("OPS_RUN_IDS"))
    if not all(_RUN_ID.match(r) for r in run_ids):
        raise ValueError("OPS_RUN_IDS")
    if mode == "collect" and not run_ids:
        raise ValueError("OPS_RUN_IDS 필요(collect)")
    applicants = _csv(env.get("OPS_APPLICANTS"))
    if mode == "submit" and not applicants:
        raise ValueError("OPS_APPLICANTS 필요(submit)")
    source = env.get("OPS_SOURCE", "load")
    if source not in SOURCES:
        raise ValueError(f"OPS_SOURCE: {source}")
    if mode != "collect":
        # 번호가 가장 큰(가장 긴) application_id까지 진입 패턴을 만족해야 한다.
        last = count if mode == "load" else len(applicants)
        if not _APPLICATION_ID.match(message(run_id, last, "", datetime.now(UTC), source)["application_id"]):
            raise ValueError("OPS_RUN_ID: application_id 패턴 위반")
    # 대기 기본값 1500초: 최악 2·대기 + 47·15초 = 3705초가 job-ops replicaTimeout(5400초) 안에 든다.
    return Config(
        mode=mode, run_id=run_id, count=count, interval_s=float(env.get("OPS_INTERVAL_S", 15)),
        applicants=applicants, source=source,
        run_ids=run_ids, wait_s=float(env.get("OPS_WAIT_S", 1500)), out_dir=out_dir,
    )


def message(run_id: str, i: int, applicant_id: str, now: datetime, source: str) -> dict[str, Any]:
    return {
        "application_id": f"{run_id.lower()}-{i:02d}",
        "applicant_id": applicant_id,
        "requested_amount_krw": _LOAD_AMOUNTS.get(applicant_id, DEFAULT_AMOUNT_KRW),
        "requested_tenor_months": TENOR_MONTHS,
        "submitted_at": now.isoformat(),
        "source": source,
        "run_id": run_id,
    }


def done(doc: dict[str, Any]) -> bool:
    return doc.get("status") == "failed" or any(
        t.get("status") == "awaiting_officer" for t in doc.get("timeline") or []
    )


def _wait(store: Store, run_id: str, ids: set[str], deadline: datetime, clock, sleep) -> list[dict[str, Any]]:
    while True:
        docs = [d for d in store.by_run(run_id) if d.get("application_id") in ids]
        if len(docs) == len(ids) and all(done(d) for d in docs):
            return docs
        if clock() >= deadline:
            return docs
        sleep(POLL_S)


def _guard(docs: list[dict[str, Any]], count: int, prices: dict) -> dict[str, Any]:
    cost = ai_cost([project(d) for d in docs], prices)
    per_app = cost["per_application_usd"] or 0.0
    # 단가 없는 호출은 비용 0으로 잡혀 과소 추정이 된다. 중단 사유는 아니고 기록만 한다.
    return {"sample": len(docs), "per_application_usd": per_app, "projected_usd": per_app * count,
            "limit_usd": BUDGET_GUARD_USD, "unpriced_calls": cost["unpriced_calls"]}


def _write(cfg: Config, runs: dict[str, list[dict[str, Any]]], summary: dict[str, Any]) -> list[Path]:
    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    docs_path, summary_path = cfg.out_dir / "docs.json", cfg.out_dir / "summary.json"
    docs_path.write_text(json.dumps({"runs": runs}, ensure_ascii=False), encoding="utf-8")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    return [docs_path, summary_path]


def run(
    cfg: Config,
    *,
    queue: Queue,
    store: Store,
    prices: dict[str, dict[str, Any]],
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    if cfg.mode == "collect":
        runs = {r: [project(d) for d in store.by_run(r)] for r in cfg.run_ids}
        summary = {"run_id": cfg.run_id, "mode": "collect", "runs": {r: len(v) for r, v in runs.items()}}
        _write(cfg, runs, summary)
        return summary

    applicants = (
        [a.applicant_id for a in LOAD_GRID[: cfg.count]] if cfg.mode == "load" else list(cfg.applicants)
    )
    sent: list[dict[str, Any]] = []
    guard, aborted = None, False
    for i, applicant in enumerate(applicants, start=1):
        if i > 1 and cfg.interval_s:
            sleep(cfg.interval_s)
        m = message(cfg.run_id, i, applicant, clock(), cfg.source)
        queue.send(json.dumps(m, ensure_ascii=False))
        sent.append(m)
        if cfg.mode == "load" and i == GUARD_SAMPLE and len(applicants) > GUARD_SAMPLE:
            ids = {s["application_id"] for s in sent}
            sample = _wait(store, cfg.run_id, ids, clock() + timedelta(seconds=cfg.wait_s), clock, sleep)
            guard = _guard(sample, len(applicants), prices)
            if guard["projected_usd"] > BUDGET_GUARD_USD:
                aborted = True
                break
    ids = {s["application_id"] for s in sent}
    docs = _wait(store, cfg.run_id, ids, clock() + timedelta(seconds=cfg.wait_s), clock, sleep)
    by_id = {d["application_id"]: d for d in docs}
    unreached = [
        {"application_id": a, "status": (by_id.get(a) or {}).get("status")}
        for a in sorted(ids)
        if a not in by_id or not done(by_id[a])
    ]
    summary = {
        "run_id": cfg.run_id,
        "mode": cfg.mode,
        "submitted": len(sent),
        "reached": len(ids) - len(unreached),
        "unreached": unreached,
        "aborted": aborted,
        "guard": guard,
    }
    _write(cfg, {cfg.run_id: [project(d) for d in docs]}, summary)
    return summary
