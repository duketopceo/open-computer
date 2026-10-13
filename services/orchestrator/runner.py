"""Worker loop — claim inbox tasks, execute, tombstone (U6).

work_fn is the injectable execution surface: a conformance stub, an
in-sandbox model call, or a shell runner — the orchestrator does not
care. A worker exception fails the task CLOSED: status=failed, no
tombstone, audit row. Nothing silently retries; nothing fabricates.
"""

from __future__ import annotations

import json
from typing import Callable

from oc.fsproto import AlreadyLeased


def run_once(fs, audit, agent: str, work_fn: Callable[[dict], object], ttl: float = 60) -> list[str]:
    """One sweep of agent's inbox. Returns the task ids attempted."""
    attempted = []
    for tid in fs.inbox_ids(agent):
        if not fs.pending(tid):  # leased, tombstoned, or failed — not ours
            continue
        try:
            fs.claim(agent, tid, ttl=ttl)
        except AlreadyLeased:
            continue  # another worker won between pending() and claim()
        attempted.append(tid)
        task = json.loads(fs.task_body(tid))
        try:
            result = work_fn(task)
        except Exception as e:
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
            continue
        fs.outbox_write(agent, tid, result)
        audit.append({"event": "worker_done", "task": tid})
    return attempted
