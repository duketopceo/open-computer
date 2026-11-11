"""Activity implementations — run inside the worker process.

Each is an honest orchestrator sweep against on-disk run state:
a failure retries per policy, then the workflow fails and the
run's disk state remains the truth — nothing fabricated.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

try:
    from temporalio import activity
except ImportError:  # the worker extra is optional; the sweep function still runs in tests
    class _Activity:
        @staticmethod
        def defn(fn):
            return fn

    activity = _Activity()

from services.orchestrator import Orchestrator
from services.orchestrator.egress import live_egress
from services.orchestrator.engines import decompose_engine, work_fn as _engine_work_fn
from services.orchestrator.privacy import live_privacy
from services.orchestrator.runcfg import create_ledger, ledger_for, model_of
from services.opsview.effort import resolve as resolve_effort
from services.placement import place_live


HELD = -1  # work_sweep result: steps are waiting on the run's egress approval (not finished, not failed)


def _recover(run_dir: str) -> tuple[Orchestrator, str | None]:
    """Fresh-process view of a run: plan.json + audit rows on disk."""
    orch = Orchestrator(run_dir, place_fn=place_live, ledger=ledger_for(run_dir), egress=live_egress(),
                        privacy=live_privacy())
    rid = next(
        (r["run"] for r in reversed(orch.audit.rows()) if r.get("event") == "run_submit"),
        None,
    )
    if rid is None or not orch.recover(rid):
        return orch, None
    model = model_of(orch.audit.rows())
    orch._engines.setdefault(rid, decompose_engine(model) if model else decompose_engine())
    if model and not orch.effort_model:
        orch.effort_model = model
    return orch, rid


@activity.defn
def work_sweep(run_dir: str) -> int:
    """One sweep: claim pending, execute, tombstone, dispatch unblocked.
    Returns completed-task count (0 = nothing pending), or HELD while steps wait on an egress approval."""
    orch, rid = _recover(run_dir)
    model = model_of(orch.audit.rows())  # a run started with an effort keeps its model across workers
    done = orch.work(_engine_work_fn(model) if model else _engine_work_fn())
    if not done and orch.retry_delay() > 0:
        return HELD  # a step is backing off after a sandbox boot failure: the workflow sleeps, then sweeps again
    if not done and rid:
        if orch.pii_pending(rid):
            return HELD
        if orch.wait_for_egress(rid, timeout=0):  # decided since the sweep began: held steps dispatched now
            return 1
        if orch.egress_pending(rid):
            return HELD  # never block an activity thread on a human: the workflow sleeps and sweeps again
    return len(done)


def _daily_cap() -> float:
    return float(os.environ.get("OC_DAILY_CAP", "5.0"))


@activity.defn
def submit_goal(
    goal: str,
    by: str,
    effort: str | None = None,
    model: str | None = None,
    budget: float | None = None,
) -> dict:
    """Automation fire: new run dir + decompose + first dispatch wave.

    Mirrors goalruns.submit minus the drive — GoalCronWorkflow then runs
    GoalRunWorkflow as a child. A decompose failure propagates: the fire
    records as a failed action in the schedule's history, never silent.

    Missing effort, model, and budget use the console default. A budget above
    the daily cap is clamped. An in-flight workflow that still passes only
    goal and actor hits the defaults.
    """
    rid = "goal_" + uuid.uuid4().hex[:8]
    goals = Path(os.environ.get("OC_GOALS_DIR", str(Path.home() / ".local/share/oc/goals")))
    run_dir = goals / rid
    run_dir.mkdir(parents=True, exist_ok=True)
    plan = resolve_effort(effort, model, budget, _daily_cap(), clamp=True)
    ledger = create_ledger(run_dir, rid, plan.budget, by)
    orch = Orchestrator(
        run_dir, place_fn=place_live, ledger=ledger, egress=live_egress(),
        privacy=live_privacy(), effort_model=plan.model,
    )
    orch.audit.append({
        "event": "effort_set", "run": rid, "effort": plan.effort, "model": plan.model, "budget": plan.budget,
    })
    orch.submit(goal, decompose_engine(plan.model, effort=plan.effort), run_id=rid)
    orch.audit.append({"event": "goal_by", "run": rid, "by": by})
    return {"run_dir": str(run_dir), "run_id": rid}


@activity.defn
def run_status(run_dir: str, run_id: str) -> dict:
    """Literal collected state — the workflow's terminal check."""
    orch = Orchestrator(run_dir, place_fn=place_live, privacy=live_privacy())
    orch.recover(run_id)
    res = orch.results(run_id)
    return {
        "states": {k: v["state"] for k, v in res.items()},
        "done": bool(res) and all(v["state"] in ("DONE", "FAILED") for v in res.values()),
    }
