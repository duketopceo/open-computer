"""Temporal workflow definition — sandbox-pure (no heavy imports).

fsproto remains the state protocol. Temporal owns scheduling, retries,
and crash-resume of the drive loop; each activity is one orchestrator
sweep against the same on-disk run state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

# Activity impls live in services/durable/activities.py (sandbox-safe passthrough).
with workflow.unsafe.imports_passed_through():
    from .activities import run_status, work_sweep  # noqa: F401  (impls run in worker)

TASK_QUEUE = "oc-goals"


@dataclass
class GoalRunInput:
    run_dir: str
    run_id: str
    max_sweeps: int = 400


@workflow.defn
class GoalRunWorkflow:
    """Drive a goal run to terminal state: status check, sweep, repeat.
    continue_as_new keeps workflow history bounded on long runs."""

    @workflow.run
    async def run(self, inp: GoalRunInput) -> dict:
        for _ in range(inp.max_sweeps):
            st = await workflow.execute_activity(
                run_status,
                args=[inp.run_dir, inp.run_id],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            if st["done"]:
                return st
            n = await workflow.execute_activity(
                work_sweep,
                args=[inp.run_dir],
                start_to_close_timeout=timedelta(seconds=300),
                heartbeat_timeout=timedelta(seconds=60),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            if n == 0:
                # nothing pending: terminal or dep-blocked — literal either way
                return await workflow.execute_activity(
                    run_status,
                    args=[inp.run_dir, inp.run_id],
                    start_to_close_timeout=timedelta(seconds=30),
                )
        await workflow.continue_as_new(GoalRunInput(inp.run_dir, inp.run_id, inp.max_sweeps))
