"""Temporal worker: registers GoalRunWorkflow + its activities.

One process — `python -m services.durable.worker` — carrying the same env
as opsview (OPENROUTER_API_KEY, CUBE_API_*) since activities execute the
same engines.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import os

from temporalio.client import Client
from temporalio.worker import Worker

from .activities import run_status, submit_goal, work_sweep
from .workflows import TASK_QUEUE, GoalCronWorkflow, GoalRunWorkflow


async def serve() -> None:
    host = os.environ.get("OC_TEMPORAL_HOST", "127.0.0.1:7233")
    ns = os.environ.get("OC_TEMPORAL_NAMESPACE", "default")
    client = await Client.connect(host, namespace=ns)
    w = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[GoalRunWorkflow, GoalCronWorkflow],
        activities=[work_sweep, run_status, submit_goal],
        activity_executor=concurrent.futures.ThreadPoolExecutor(max_workers=8),
    )
    await w.run()


if __name__ == "__main__":
    asyncio.run(serve())
