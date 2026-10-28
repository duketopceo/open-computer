"""Worker loop — claim inbox tasks, execute, tombstone (U6).

work_fn is the injectable execution surface: a conformance stub, an
in-sandbox model call, or a shell runner — the orchestrator does not
care. A worker exception fails the task CLOSED: status=failed, no
tombstone, audit row. Nothing silently retries; nothing fabricates.
"""

from __future__ import annotations

import json
import time
from typing import Callable

from dataclasses import dataclass

from oc.fsproto import AlreadyLeased
from oc.obs import bind, error_fields, get_logger
from services.cost import CostLedger, Usage
from services.telemetry import report_failure

log = get_logger("worker")


def _ctx(task: dict, tid: str):
    """Bind the id spine carried on the task body for the duration of a log/work block."""
    return bind(run_id=task.get("run"), subtask=task.get("subtask"), task=tid, chat_id=task.get("chat"),
                service="worker")


@dataclass
class WorkResult:
    """What a metering-aware work_fn returns: the output plus the provider-reported usage."""

    output: object
    usage: Usage | None = None


def run_once(
    fs,
    audit,
    agent: str,
    work_fn: Callable[[dict], object],
    ttl: float = 60,
    ledger: CostLedger | None = None,
) -> list[str]:
    """One sweep of agent's inbox. Returns the task ids attempted.

    With a ledger: a task whose run is over its cap is NOT started (left queued, never failed or
    cancelled), and usage returned via WorkResult is recorded."""
    attempted = []
    for tid in fs.inbox_ids(agent):
        if not fs.pending(tid):  # leased, tombstoned, or failed — not ours
            continue
        task = json.loads(fs.task_body(tid))
        with _ctx(task, tid):  # every log line below (ledger included) carries the run/subtask/task/chat ids
            if _run_task(fs, audit, agent, work_fn, ttl, ledger, tid, task):
                attempted.append(tid)
    return attempted


def _run_task(fs, audit, agent, work_fn, ttl, ledger, tid: str, task: dict) -> bool:
    """One inbox task. True when it was claimed (and so attempted), False when it was held or lost the claim."""
    token = None
    if ledger is not None:
        token = ledger.check(task["run"], float(task.get("est_cost", 0.0)))
        if token is None:
            audit.append({"event": "held_cost_cap", "task": tid, "run": task["run"]})
            log.warning("task held: run over cost cap", agent=agent)
            return False  # paused: stays queued until the owner resumes
    try:
        fs.claim(agent, tid, ttl=ttl)
    except AlreadyLeased:
        if token:
            ledger.release(task["run"], token)
        return False  # another worker won between pending() and claim()
    t0 = time.monotonic()
    try:
        log.info("task claimed", agent=agent, placement=task.get("placement"), engine=task.get("engine"))
        result = work_fn(task)
    except Exception as e:
        log.error("task failed", agent=agent, ms=round((time.monotonic() - t0) * 1000, 1), **error_fields(e))
        if token:
            ledger.release(task["run"], token)
        fs.fail(tid)
        audit.append(
            {
                "event": "worker_fail",
                "task": tid,
                "kind": type(e).__name__,
                "error": str(e),
                "hint": getattr(e, "hint", ""),
            }
        )
        report_failure(
            f"{type(e).__name__}: {e}",
            body=getattr(e, "hint", "") or None,
            fingerprint=f"{type(e).__name__}",
            exc=e,
            run=task["run"], task=tid, subtask=task.get("subtask", ""),
        )
        return True
    if isinstance(result, WorkResult) and result.usage is not None and ledger is not None:
        ledger.record(task["run"], task["subtask"], result.usage, token=token)
    elif token:
        ledger.release(task["run"], token)  # no usage reported: nothing was spent
    if isinstance(result, WorkResult):
        result = result.output
    fs.outbox_write(agent, tid, result)
    audit.append({"event": "worker_done", "task": tid})
    log.info("task done", agent=agent, ms=round((time.monotonic() - t0) * 1000, 1))
    return True
