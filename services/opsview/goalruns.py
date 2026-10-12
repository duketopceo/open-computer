"""Goal run registry — what the goals surface reads (F2).

One dir per run under OC_GOALS_DIR: the orchestrator's fsproto tree,
gate audit, and orchestrator.ndjson are the ONLY state. list_runs /
get_run read files — a run the orchestrator forgot still renders its
literal disk state, never a fabricated status.

(Distinct from runs.py — that store is matrix conformance history.)
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from oc.fsproto import adapter as fs_adapter
from services.orchestrator import Orchestrator

try:  # temporalio is an optional dep — opsview runs without it
    from services.durable import client as durable
except ImportError:
    durable = None

_STATES = ("QUEUED", "LEASED", "RUNNING", "DONE", "FAILED", "AWAITING-APPROVAL")


def runs_dir() -> Path:
    return Path(os.environ.get("OC_GOALS_DIR", str(Path.home() / ".local/share/oc/goals")))

_live: dict[str, Orchestrator] = {}
_lock = threading.Lock()


def _audit_rows(run_dir: Path) -> list[dict]:
    f = run_dir / "orchestrator.ndjson"
    if not f.exists():
        return []
    out = []
    for line in f.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _task_state(run_dir: Path, tid: str) -> str:
    sf = run_dir / "run" / "tasks" / tid / "status"
    if sf.exists():
        return sf.read_text().strip().upper()
    return "QUEUED"


def list_runs() -> list[dict]:
    """Recent runs, newest first — literal disk state only."""
    base = runs_dir()
    if not base.exists():
        return []
    out = []
    for d in sorted(base.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not d.is_dir():
            continue
        rows = _audit_rows(d)
        submit = next((r for r in rows if r.get("event") == "run_submit"), None)
        if submit is None:
            continue
        states = {s: 0 for s in _STATES}
        for r in rows:
            if r.get("event") == "dispatch":
                st = _task_state(d, r["task"])
                states[st] = states.get(st, 0) + 1
        undispatched = submit.get("subtasks", 0) - sum(states.values())
        states["QUEUED"] += max(0, undispatched)
        out.append(
            {
                "run": d.name,
                "goal": submit.get("goal", ""),
                "subtasks": submit.get("subtasks", 0),
                "states": states,
                "ts": submit.get("ts", d.stat().st_mtime),
            }
        )
        if len(out) >= 30:
            break
    return out


def get_run(run_id: str) -> dict | None:
    """One run: goal, dispatch records, per-subtask state + results."""
    d = runs_dir() / run_id
    if not d.is_dir():
        return None
    rows = _audit_rows(d)
    submit = next((r for r in rows if r.get("event") == "run_submit"), None)
    fs = fs_adapter(d / "run")
    subs = []
    for r in rows:
        if r.get("event") != "dispatch":
            continue
        tid = r["task"]
        state = _task_state(d, tid)
        try:
            body = json.loads(fs.task_body(tid))
        except Exception:
            body = {}
        rec = {
            "subtask": r.get("subtask"),
            "title": body.get("title", ""),
            "state": state,
            "route": r.get("route"),
            "engine": r.get("engine"),
            "placement": r.get("placement"),
            "reason": r.get("reason"),
            "gate_required": r.get("gate_required"),
            "cost": r.get("cost"),
            "pii": r.get("pii"),
        }
        if state == "DONE":
            try:
                rec["result"] = fs.collect("orchestrator", tid)
            except Exception:
                rec["state"] = "FAILED"  # tombstoned but unreadable = corrupted
        subs.append(rec)
    fails = [r for r in rows if r.get("event") == "worker_fail"]
    return {
        "run": run_id,
        "goal": (submit or {}).get("goal", ""),
        "subtasks": subs,
        "declared": (submit or {}).get("subtasks", 0),
        "worker_failures": fails,
        "started": (submit or {}).get("ts"),
    }


def submit(
    goal: str,
    engine: Callable[[str], str],
    wfn: Callable[[dict], object],
    *,
    by: str = "",
) -> str:
    """Create a run dir, submit, and drive it on a daemon thread."""
    rid = "goal_" + uuid.uuid4().hex[:8]
    run_dir = runs_dir() / rid
    run_dir.mkdir(parents=True, exist_ok=True)
    orch = Orchestrator(run_dir)
    with _lock:
        _live[rid] = orch
    orch.submit(goal, engine, run_id=rid)
    orch.audit.append({"event": "goal_by", "run": rid, "by": by})

    wf = durable.start_durable(run_dir, rid) if durable else None
    if wf:
        orch.audit.append({"event": "durable_start", "run": rid, "workflow": wf})
    else:
        _start_drive(orch, rid, wfn)
    return rid


def _drive(orch: Orchestrator, rid: str, wfn: Callable[[dict], object]) -> None:
    try:
        for _ in range(400):
            res = orch.results(rid)
            if res and all(r["state"] in ("DONE", "FAILED") for r in res.values()):
                break
            if not orch.work(wfn):
                break
    except Exception:
        pass  # the audit rows + task files on disk remain the truth


def _start_drive(orch: Orchestrator, rid: str, wfn) -> None:
    threading.Thread(target=_drive, args=(orch, rid, wfn), daemon=True, name=f"oc-goal-{rid}").start()


def resume(wfn) -> int:
    """Re-drive runs left non-terminal by a process restart. Count resumed.

    Orchestrator.recover rebuilds in-memory state from plan.json + audit
    rows; runs with no plan (pre-plan or mid-decompose crash) are left
    literal on disk — reported, never fabricated.
    """
    base = runs_dir()
    if not base.exists():
        return 0
    n = 0
    for d in sorted(base.iterdir()):
        if not d.is_dir():
            continue
        if durable and durable.enabled() and any(
            r.get("event") == "durable_start" for r in _audit_rows(d)
        ):
            continue  # Temporal owns this run's drive loop
        try:
            orch = Orchestrator(d)
            if not orch.recover(d.name):
                continue
            res = orch.results(d.name)
            if res and all(r["state"] in ("DONE", "FAILED") for r in res.values()):
                continue
            with _lock:
                _live[d.name] = orch
            _start_drive(orch, d.name, wfn)
            n += 1
        except Exception:
            continue  # a corrupt run stays literal on disk
    return n
