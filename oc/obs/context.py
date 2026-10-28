"""Correlation ids carried through a run: one spine for logs, call records, ledger and OpenRouter requests.

`bind(...)` sets fields for the current context (thread / task) and restores the previous values on exit.
`trace_id` is derived from `run_id` (sha256, 32 hex = a valid W3C/OTel trace id), so a restarted process or a
separate worker lands on the same trace without any shared state. Across a process boundary the ids travel as
plain fields on the fsproto task body; the receiving side calls `bind` again.
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

FIELDS = ("trace_id", "run_id", "goal", "subtask", "task", "chat_id", "service", "request_id")

_ctx: ContextVar[dict] = ContextVar("oc_obs_ctx", default={})


def trace_id_for(run_id: str) -> str:
    return hashlib.sha256(f"oc-run:{run_id}".encode()).hexdigest()[:32]


def current() -> dict:
    """The bound ids (only the ones that are set)."""
    return dict(_ctx.get())


@contextmanager
def bind(**fields) -> Iterator[dict]:
    """Bind ids for the duration of the block. Unknown keys are ignored; None values are skipped.

    Binding `run_id` without a `trace_id` derives one; `chat_id` falls back to the run id so every run is
    attributable to *some* chat even when the caller has no conversation concept."""
    new = {k: str(v) for k, v in fields.items() if k in FIELDS and v is not None and v != ""}
    merged = {**_ctx.get(), **new}
    if "run_id" in new and "trace_id" not in new:
        merged["trace_id"] = trace_id_for(merged["run_id"])
    if "run_id" in merged and "chat_id" not in merged:
        merged["chat_id"] = merged["run_id"]
    token = _ctx.set(merged)
    try:
        yield dict(merged)
    finally:
        _ctx.reset(token)
