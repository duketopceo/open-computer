"""Activity implementations — run inside the worker process.

Each is an honest orchestrator sweep against on-disk run state:
a failure retries per policy, then the workflow fails and the
run's disk state remains the truth — nothing fabricated.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from temporalio import activity

from services.orchestrator import Orchestrator
from services.orchestrator.engines import decompose_engine, work_fn as _engine_work_fn
from services.orchestrator.runcfg import ledger_for, model_of
from services.placement import place_live


def _recover(run_dir: str) -> tuple[Orchestrator, str | None]:
    """Fresh-process view of a run: plan.json + audit rows on disk."""
    orch = Orchestrator(run_dir, place_fn=place_live, ledger=ledger_for(run_dir))
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
    model = model_of(orch.audit.rows())  # a run started with an effort keeps its model across workers
    return len(orch.work(_engine_work_fn(model) if model else _engine_work_fn()))


@activity.defn
def submit_goal(goal: str, by: str) -> dict:
    """Automation fire: new run dir + decompose + first dispatch wave.

    Mirrors goalruns.submit minus the drive — GoalCronWorkflow then runs
    GoalRunWorkflow as a child. A decompose failure propagates: the fire
    records as a failed action in the schedule's history, never silent.
    """
    rid = "goal_" + uuid.uuid4().hex[:8]
    goals = Path(os.environ.get("OC_GOALS_DIR", str(Path.home() / ".local/share/oc/goals")))
    run_dir = goals / rid
    run_dir.mkdir(parents=True, exist_ok=True)
    orch = Orchestrator(run_dir, place_fn=place_live)
    orch.submit(goal, decompose_engine(), run_id=rid)
    orch.audit.append({"event": "goal_by", "run": rid, "by": by})
    return {"run_dir": str(run_dir), "run_id": rid}


@activity.defn
def run_status(run_dir: str, run_id: str) -> dict:
    """Literal collected state — the workflow's terminal check."""
    orch = Orchestrator(run_dir, place_fn=place_live)
    orch.recover(run_id)
    res = orch.results(run_id)
    return {
        "states": {k: v["state"] for k, v in res.items()},
        "done": bool(res) and all(v["state"] in ("DONE", "FAILED") for v in res.values()),
    }
