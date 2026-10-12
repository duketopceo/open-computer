"""Activity implementations — run inside the worker process.

Each is an honest orchestrator sweep against on-disk run state:
a failure retries per policy, then the workflow fails and the
run's disk state remains the truth — nothing fabricated.
"""

from __future__ import annotations

from temporalio import activity

from services.orchestrator import Orchestrator
from services.orchestrator.engines import work_fn as _engine_work_fn


def _recover(run_dir: str) -> tuple[Orchestrator, str | None]:
    """Fresh-process view of a run: plan.json + audit rows on disk."""
    orch = Orchestrator(run_dir)
    rid = next(
        (r["run"] for r in reversed(orch.audit.rows()) if r.get("event") == "run_submit"),
        None,
    )
    if rid is None or not orch.recover(rid):
        return orch, None
    return orch, rid


@activity.defn
def work_sweep(run_dir: str) -> int:
    """One sweep: claim pending, execute, tombstone, dispatch unblocked.
    Returns completed-task count (0 = nothing pending)."""
    orch, _ = _recover(run_dir)
    return len(orch.work(_engine_work_fn()))


@activity.defn
def run_status(run_dir: str, run_id: str) -> dict:
    """Literal collected state — the workflow's terminal check."""
    orch = Orchestrator(run_dir)
    orch.recover(run_id)
    res = orch.results(run_id)
    return {
        "states": {k: v["state"] for k, v in res.items()},
        "done": bool(res) and all(v["state"] in ("DONE", "FAILED") for v in res.values()),
    }
