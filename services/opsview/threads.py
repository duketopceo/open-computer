"""Chat threads: the thing the owner talks to.

One JSON file per thread under OC_THREADS_DIR. A thread holds the messages (user, assistant, and `run`
messages that point at a goal run the orchestrator started for it). Runs stay in goalruns; a thread only
links to them, so the run view, approvals and cost cap all work unchanged inside a thread.

A turn runs on a daemon thread: ask the model, and if it chooses `run`, start the run, wait for it,
show the model what it produced, and ask again, up to MAX_PASSES runs per user message.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from services import compact as oc_compact
from services import memory as oc_memory
from services.opsview import goalruns
from services.orchestrator import chat

ID_RE = re.compile(r"th_[0-9a-f]{8}")
TEXT_MAX = 4000
TITLE_MAX = 60
WAIT_TIMEOUT_S = 1800.0

_lock = threading.RLock()
_working: set[str] = set()


class ThreadBusy(RuntimeError):
    """A turn or a run in this thread is still going."""


def threads_dir() -> Path:
    return Path(os.environ.get("OC_THREADS_DIR", str(Path.home() / ".local/share/oc/threads")))


def _path(tid: str) -> Path | None:
    return threads_dir() / f"{tid}.json" if ID_RE.fullmatch(tid or "") else None


def _read(tid: str) -> dict | None:
    p = _path(tid)
    if p is None or not p.exists():
        return None
    try:
        t = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    return t if isinstance(t, dict) and isinstance(t.get("messages"), list) else None


def _write(t: dict) -> None:
    p = _path(t["id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(t))
    tmp.replace(p)  # a reader never sees half a thread


def create(text: str, plan: dict) -> dict:
    """A new thread whose first message is `text`. `plan` is the resolved effort {effort, model, budget}."""
    text = text.strip()
    now = time.time()
    t = {
        "id": "th_" + uuid.uuid4().hex[:8],
        "title": text.replace("\n", " ")[:TITLE_MAX],
        "created": now,
        "updated": now,
        "archived": False,
        "effort": plan,
        "messages": [{"role": "user", "text": text, "ts": now}],
    }
    with _lock:
        _write(t)
    return t


def get(tid: str) -> dict | None:
    return _read(tid)


def append(tid: str, role: str, text: str, **extra) -> dict | None:
    with _lock:
        t = _read(tid)
        if t is None:
            return None
        t["messages"].append({"role": role, "text": text, "ts": time.time(), **extra})
        t["updated"] = time.time()
        _write(t)
        return t


def is_working(tid: str) -> bool:
    return tid in _working


def run_ids(t: dict) -> list[str]:
    return [m["run"] for m in t["messages"] if m["role"] == "run"]


def _unfinished_runs() -> set[str]:
    """Runs on disk that could still progress or are being driven."""
    return {r["run"] for r in goalruns.list_runs(include_archived=True, limit=None) if not r["finished"]}


def summary(t: dict, unfinished: set[str] | None = None) -> dict:
    unfinished = _unfinished_runs() if unfinished is None else unfinished
    working = is_working(t["id"])
    return {
        "id": t["id"],
        "title": t["title"],
        "ts": t["updated"],
        "archived": bool(t.get("archived")),
        "working": working,
        "finished": not working and not unfinished.intersection(run_ids(t)),
    }


def list_threads(include_archived: bool = False) -> list[dict]:
    base = threads_dir()
    if not base.exists():
        return []
    out = []
    for p in base.glob("th_*.json"):
        t = _read(p.stem)
        if t is not None and (include_archived or not t.get("archived")):
            out.append(t)
    out.sort(key=lambda t: t["updated"], reverse=True)
    unfinished = _unfinished_runs()
    return [summary(t, unfinished) for t in out]


def linked_run_ids() -> set[str]:
    """Every run some thread started. The rail shows these inside their thread, not as loose runs."""
    base = threads_dir()
    out: set[str] = set()
    for p in base.glob("th_*.json") if base.exists() else ():
        t = _read(p.stem)
        if t is not None:
            out.update(run_ids(t))
    return out


def detail(tid: str) -> dict | None:
    """The whole thread plus every run it links to, so one fetch renders the page."""
    t = _read(tid)
    if t is None:
        return None
    out = {**t, "working": is_working(tid), "runs": {rid: goalruns.get_run(rid) for rid in dict.fromkeys(run_ids(t))}}
    if oc_memory.MemoryConfig.from_env() is not None:
        out["memory"] = oc_memory.thread_view(tid)
    return out


def delete(tid: str) -> bool:
    """Remove a thread and the runs it started. ThreadBusy while a turn or one of its runs is live."""
    t = _read(tid)
    if t is None:
        return False
    if is_working(tid) or any(goalruns._driving(r) for r in run_ids(t)):
        raise ThreadBusy(tid)
    for rid in run_ids(t):
        goalruns.delete_run_quiet(rid)
    with _lock:
        _path(tid).unlink(missing_ok=True)
    return True


def archive(tid: str, on: bool = True) -> bool:
    with _lock:
        t = _read(tid)
        if t is None:
            return False
        t["archived"] = on
        _write(t)
    return True


def clear_finished() -> int:
    n = 0
    for s in list_threads(include_archived=True):
        if s["finished"]:
            try:
                n += delete(s["id"])
            except ThreadBusy:
                pass
    return n


# ----------------------------------------------------------------------------- turns


@dataclass
class Deps:
    engine: Callable[[list[dict]], str]  # whole thread in, raw model text out
    start_run: Callable[[str], str]  # goal in, run id out
    load_run: Callable[[str], dict | None]
    wait_run: Callable[[str], str]  # blocks; returns done | approval | paused | timeout
    memory: oc_memory.Memory | None = None  # off unless OC_MEMORY=kurultai
    trace: Callable[[str, str, dict], None] | None = None  # (run id, event type, data) onto the run's stream
    compact: oc_compact.Compactor | None = None  # long threads: older messages compacted, restorable by range


def _last_user(t: dict) -> dict | None:
    return next((m for m in reversed(t.get("messages", [])) if m.get("role") == "user"), None)


def _run_trace(rid: str, type_: str, data: dict) -> None:
    """Memory events on a run's dev-trace stream. A no-op when the run has no stream."""
    from oc.obs import devtrace

    tr = devtrace.tracer(run_id=rid, actor="orchestrator")
    if tr is not None:
        tr.emit(type_, id=devtrace.entity("mem", f"{type_}_{int(time.time() * 1000)}"), **data)


def wait_for_run(rid: str, *, timeout: float = WAIT_TIMEOUT_S, poll: float = 1.0) -> str:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        view = goalruns.get_run(rid)
        status = chat.run_status(view) if view else "failed"
        if status in ("done", "failed"):
            return "done"
        if status == "waiting for approval":
            return "plan" if ((view or {}).get("plan") or {}).get("state") == "planned" else "approval"
        if status.startswith("paused"):
            return "paused"
        time.sleep(poll)
    return "timeout"


_STOP_NOTES = {
    # Approving the plan in the card resumes this chat (resume_after_plan), so no "tell me to carry on".
    "plan": "The plan is ready in the run card. Approve it there and I'll carry on.",
    "approval": "A step needs your approval. Open Approvals from the run card, then tell me to carry on.",
    "paused": "The run hit its budget and paused. Nothing was cancelled. Tell me to carry on if you want to spend more.",
    "timeout": "The run is taking a long time. It keeps going in the background; ask me for an update.",
}


MAX_RESTORES = 2  # restored spans per user message; bounds a model that keeps asking


def run_turn(tid: str, d: Deps, max_passes: int = chat.MAX_PASSES, waiting_on: str | None = None) -> None:
    """Drive one user message to a reply. Never raises: a failure becomes an assistant message.

    `waiting_on` resumes a turn that stopped on a held plan: wait for that run first, then answer as usual."""
    trace = d.trace or _run_trace
    try:
        if waiting_on is not None:
            stop = d.wait_run(waiting_on)
            if stop != "done":
                append(tid, "assistant", _STOP_NOTES[stop])
                return
        hits: list[oc_memory.Hit] = []
        first = _read(tid)
        user = _last_user(first) if first else None
        if d.memory is not None and user is not None:
            # One recall per user message, before the first pass; every pass of this turn sees the same notes.
            hits = d.memory.recall(user["text"], oc_memory.Scope(tid), user_ts=user.get("ts"))
        block = oc_memory.render_block(hits)
        last_run: str | None = waiting_on
        restored: list[dict] = []  # this turn's restored spans, shown after the thread on every later call
        n = 0
        while n <= max_passes:
            t = _read(tid)
            if t is None:
                return
            final = n == max_passes
            runs = {rid: d.load_run(rid) for rid in dict.fromkeys(run_ids(t))}
            msgs = chat.build_messages(t, runs, final=final, memory=block)
            if d.compact is not None:
                msgs = d.compact.fit(tid, msgs).messages
            dec = chat.parse_decision(d.engine(msgs + restored))
            if dec.action == "restore":
                text = d.compact.restore(tid, dec.spans) if d.compact is not None else None
                if len(restored) < MAX_RESTORES:
                    body = text if text is not None else "Nothing to restore for that range."
                    restored.append({"role": "system", "content": f"{chat.RESTORE_NOTE}\n[{dec.spans}]\n{body}"})
                    continue  # a restore is not a pass: the model answers again with the span in view
                dec = chat.Decision("reply", dec.text or "I could not finish that: too many restores in one turn.")
            n += 1
            if dec.action != "run" or final:
                append(tid, "assistant", dec.text, **({"ask": True} if dec.action == "ask" else {}))
                if d.memory is not None and last_run and dec.action == "reply":
                    _remember_outcome(d.memory, t, tid, last_run, dec.text, trace)
                return
            rid = d.start_run(dec.goal)
            last_run = rid
            if hits:
                trace(rid, "memory_recall", {"atoms": [h.cite() for h in hits]})
            append(tid, "assistant", dec.text)
            append(tid, "run", dec.goal, run=rid, goal=dec.goal)
            stop = d.wait_run(rid)
            if stop != "done":
                append(tid, "assistant", _STOP_NOTES[stop])
                return
    except Exception as e:
        append(tid, "assistant", f"I could not finish that: {e}", error=True)


def _remember_outcome(mem: oc_memory.Memory, t: dict, tid: str, rid: str, reply: str,
                      trace: Callable[[str, str, dict], None]) -> None:
    """After a turn whose run finished and was reported, store the report as one distilled note."""
    title = (t.get("title") or "Open Computer chat").strip()
    atom = mem.remember(title, reply, oc_memory.Scope(tid, run=rid))
    if atom:
        trace(rid, "memory_write", {"atom": atom, "title": title[: oc_memory.TITLE_MAX]})


def thread_for_run(rid: str) -> str | None:
    """The chat that started run `rid`, if any."""
    base = threads_dir()
    for p in base.glob("th_*.json") if base.exists() else ():
        t = _read(p.stem)
        if t is not None and rid in run_ids(t):
            return t["id"]
    return None


def start_turn(tid: str, d: Deps, waiting_on: str | None = None) -> bool:
    """Run a turn on a daemon thread. False if one is already running for this thread."""
    with _lock:
        if tid in _working:
            return False
        _working.add(tid)

    def go():
        try:
            run_turn(tid, d, waiting_on=waiting_on)
        finally:
            _working.discard(tid)

    threading.Thread(target=go, daemon=True, name=f"oc-turn-{tid}").start()
    return True
