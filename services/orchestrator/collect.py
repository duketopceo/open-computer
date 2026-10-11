"""Collect — gather tombstoned results; partial DAGs stay queued (U6).

Never fabricates: a subtask without a tombstone is reported as its
literal state (queued / leased / failed), not as a partial result.
"""

from __future__ import annotations

from oc.fsproto import NotReady


def task_state(fs, tid: str) -> str:
    """Literal state word — QUEUED / LEASED / DONE / FAILED."""
    status = fs._task_dir(tid) / "status"
    if status.exists():
        return status.read_text().strip().upper()
    return "QUEUED"


def gather(fs, tid) -> str:
    """One task's result body. NotReady if no tombstone — R7."""
    return fs.collect("orchestrator", tid)


def collect_run(fs, dispatches: list[dict], subtask_ids: list[str] | None = None) -> dict:
    """{subtask_id: {"state", "result"}} — result only when DONE.

    Undispatched subtasks (deps unmet) still appear — QUEUED, no task id,
    no result. Never fabricated.
    """
    out = {}
    for sid in subtask_ids or []:
        out[sid] = {"state": "QUEUED"}
    for d in dispatches:
        tid = d["task"]
        state = task_state(fs, tid)
        rec = {"state": state, "task": tid}
        if state == "DONE":
            try:
                rec["result"] = gather(fs, tid)
            except NotReady:
                rec["state"] = "QUEUED"
        out[d["subtask"]] = rec
    return out
