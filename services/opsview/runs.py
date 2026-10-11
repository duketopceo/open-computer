"""Run registry — what the work surface reads (F2).

One dir per run under OC_RUNS_DIR: the orchestrator's fsproto tree,
gate audit, and orchestrator.ndjson are the ONLY state. list_runs /
get_run read files — a run the orchestrator forgot still renders its
literal disk state, never a fabricated status.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Callable

from oc.fsproto import adapter as fs_adapter
from services.orchestrator import Orchestrator

RUNS_DIR = Path(os.environ.get("OC_RUNS_DIR", str(Path.home() / ".local/share/oc/runs")))

_STATES = ("QUEUED", "LEASED", "RUNNING", "DONE", "FAILED", "AWAITING-APPROVAL")

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
    if not RUNS_DIR.exists():
        return []
    out = []
    for d in sorted(RUNS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not d.is_dir():
            continue
        rows = _audit_rows(d)
        submit = next((r for r in rows if r.get("event") == "run_submit"), None)
        if submit is None:
            continue
        states = {s: 0 for s in _STATES}
        for r in rows:
            if r.get("event") == "dispatch":
                states.setdefault(_task_state(d, r["task"]), 0)
                states[_task_state(d, r["task"])] += 1
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
    d = RUNS_DIR / run_id
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
        rec = {
            "subtask": r.get("subtask"),
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
        "worker_failures": fails,
        "started": (submit or {}).get("ts"),
    }


def submit(
    goal: str,
    engine: Callable[[str], str],
    wfn: Callable[[dict], object],
    *,
    run_id: str | None = None,
) -> str:
    """Create a run dir, submit, and drive it on a daemon thread."""
    rid = run_id or f"{int(time.time())}-{os.getpid() % 997:03d}"
    run_dir = RUNS_DIR / rid
    run_dir.mkdir(parents=True, exist_ok=True)
    orch = Orchestrator(run_dir)
    with _lock:
        _live[rid] = orch
    orch.submit(goal, engine, run_id=rid)

    def drive():
        try:
            for _ in range(400):
                res = orch.results(rid)
                if res and all(r["state"] in ("DONE", "FAILED") for r in res.values()):
                    break
                if not orch.work(wfn):
                    break
        except Exception:
            pass  # the audit rows + task files on disk remain the truth

    threading.Thread(target=drive, daemon=True, name=f"oc-run-{rid}").start()
    return rid
