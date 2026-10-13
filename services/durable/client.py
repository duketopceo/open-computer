"""Client side: start a GoalRunWorkflow for an already-submitted run.

submit() stays synchronous in goalruns (decompose + first dispatch wave
on disk); durable mode only replaces the drive thread with a Temporal
workflow execution. If Temporal is unreachable the caller falls back —
the in-process driver + resume() are the local durability floor.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from temporalio.client import Client
from temporalio.exceptions import WorkflowAlreadyStartedError

from .workflows import TASK_QUEUE, GoalRunInput, GoalRunWorkflow


def enabled() -> bool:
    return os.environ.get("OC_DURABLE", "").lower() in ("1", "true", "yes")


async def _start(run_dir: Path, run_id: str) -> str:
    host = os.environ.get("OC_TEMPORAL_HOST", "127.0.0.1:7233")
    ns = os.environ.get("OC_TEMPORAL_NAMESPACE", "default")
    client = await Client.connect(host, namespace=ns)
    h = await client.start_workflow(
        GoalRunWorkflow.run,
        GoalRunInput(str(run_dir), run_id),
        id=f"goal-{run_id}",
        task_queue=TASK_QUEUE,
    )
    return h.id


def start_durable(run_dir: Path, run_id: str) -> str | None:
    """Start the workflow; return its id, or None when Temporal is off/unreachable."""
    if not enabled():
        return None
    try:
        return asyncio.run(_start(run_dir, run_id))
    except Exception:
        return None  # caller falls back to the in-process driver


async def _ensure(run_dir: Path, run_id: str) -> str:
    host = os.environ.get("OC_TEMPORAL_HOST", "127.0.0.1:7233")
    ns = os.environ.get("OC_TEMPORAL_NAMESPACE", "default")
    client = await Client.connect(host, namespace=ns)
    wid = f"goal-{run_id}"
    try:
        h = await client.start_workflow(
            GoalRunWorkflow.run,
            GoalRunInput(str(run_dir), run_id),
            id=wid,
            task_queue=TASK_QUEUE,
        )
        return h.id
    except WorkflowAlreadyStartedError:
        return wid  # a live execution already owns this run's drive loop


def ensure_durable(run_dir: Path, run_id: str) -> str | None:
    """Return the workflow id (just started or already running).

    None when Temporal is disabled or unreachable — the caller may then
    use the in-process driver. A completed/failed workflow id re-starts
    under the default reuse policy; a running one raises
    WorkflowAlreadyStartedError and is treated as owned.
    """
    if not enabled():
        return None
    try:
        return asyncio.run(_ensure(run_dir, run_id))
    except Exception:
        return None
