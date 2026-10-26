"""Worker loop — claim inbox tasks, execute, tombstone (U6).

work_fn is the injectable execution surface: a conformance stub, an
in-sandbox model call, or a shell runner — the orchestrator does not
care. A worker exception fails the task CLOSED: status=failed, no
tombstone, audit row. Nothing silently retries; nothing fabricates.
"""

from __future__ import annotations

import json
from typing import Callable

from dataclasses import dataclass

from oc.fsproto import AlreadyLeased
from services.cost import CostLedger, Usage
from services.telemetry import report_failure


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
        token = None
        if ledger is not None:
            token = ledger.check(task["run"], float(task.get("est_cost", 0.0)))
            if token is None:
                audit.append({"event": "held_cost_cap", "task": tid, "run": task["run"]})
                continue  # paused: stays queued until the owner resumes
        try:
            fs.claim(agent, tid, ttl=ttl)
        except AlreadyLeased:
            if token:
                ledger.release(task["run"], token)
            continue  # another worker won between pending() and claim()
        attempted.append(tid)
        try:
            result = work_fn(task)
        except Exception as e:
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
            continue
        if isinstance(result, WorkResult) and result.usage is not None and ledger is not None:
            ledger.record(task["run"], task["subtask"], result.usage, token=token)
        elif token:
            ledger.release(task["run"], token)  # no usage reported: nothing was spent
        if isinstance(result, WorkResult):
            result = result.output
        fs.outbox_write(agent, tid, result)
        audit.append({"event": "worker_done", "task": tid})
    return attempted
