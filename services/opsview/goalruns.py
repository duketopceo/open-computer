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
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from oc.fsproto import adapter as fs_adapter
from services.orchestrator import Orchestrator
from services.orchestrator.egress import live_egress, requested_hosts
from services.orchestrator.runner import MAX_STEP_ATTEMPTS
from services.orchestrator.runcfg import create_ledger, effort_row, ledger_for, model_of
from services.orchestrator.thinking import PLAN, ThinkingLog, join_thoughts
from services.placement import place_live

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


def _held_steps(run_dir: Path, rows: list[dict]) -> list[dict]:
    """Planned steps still waiting on the run's up-front egress approval: not dispatched, not failed.

    Empty unless the run asked for an approval and the queue still shows it pending (read from disk, so a
    restarted console renders the same words)."""
    from services.gate.queue import ApprovalQueue, default_approvals_path

    req = next((r for r in rows if r.get("event") == "egress_approval_requested"), None)
    plan = run_dir / "plan.json"
    if req is None or not plan.exists():
        return []
    try:
        item = ApprovalQueue(default_approvals_path()).get(req.get("approval", ""))
    except OSError:
        return []  # an unreadable queue must not take the run list down
    if item is None or item["status"] != "pending":
        return []
    sent = {r.get("subtask") for r in rows if r.get("event") == "dispatch"}
    hosts = set(req.get("hosts") or [])
    try:
        subs = json.loads(plan.read_text())
    except ValueError:
        return []
    return [
        {"sub": s, "approval": req["approval"], "hosts": sorted(hosts & set(requested_hosts(s)))}
        for s in subs
        if s["id"] not in sent and hosts & set(requested_hosts(s))
    ]


RUN_ID_RE = re.compile(r"goal_[0-9a-f]{8}")
ARCHIVED_MARK = "archived"


class RunBusy(RuntimeError):
    """The run still has a live driver in this process: stop it before removing it."""


def _driving(rid: str) -> bool:
    """True while this process has a live drive thread for `rid` (a stale queued run has none)."""
    return any(t.name == f"oc-goal-{rid}" and t.is_alive() for t in threading.enumerate())


def _run_path(run_id: str) -> Path | None:
    """The run's directory, or None for anything that is not an existing goal run under runs_dir()."""
    if not RUN_ID_RE.fullmatch(run_id or ""):
        return None
    d = runs_dir() / run_id
    return d if d.is_dir() else None


def _finished(states: dict) -> bool:
    """Nothing left that could still progress: no step queued, leased, running or waiting on approval."""
    return not any(states.get(s) for s in ("QUEUED", "LEASED", "RUNNING", "AWAITING-APPROVAL"))


def delete_run(run_id: str) -> bool:
    """Remove a run and everything it wrote. False if it does not exist; RunBusy while it is being driven."""
    d = _run_path(run_id)
    if d is None:
        return False
    if _driving(run_id):
        raise RunBusy(run_id)
    shutil.rmtree(d, ignore_errors=True)
    with _lock:
        _live.pop(run_id, None)
    return True


def archive_run(run_id: str, archived: bool = True) -> bool:
    """Hide a run from the default list (or bring it back). The run stays on disk untouched."""
    d = _run_path(run_id)
    if d is None:
        return False
    mark = d / ARCHIVED_MARK
    if archived:
        mark.touch()
    else:
        mark.unlink(missing_ok=True)
    return True


def clear_finished(keep: frozenset[str] | set[str] = frozenset()) -> int:
    """Delete every finished run that nothing is driving, except ids in `keep`. Returns how many were removed."""
    n = 0
    for r in list_runs(include_archived=True, limit=None):
        if r["run"] not in keep and _finished(r["states"]) and delete_run_quiet(r["run"]):
            n += 1
    return n


def delete_run_quiet(run_id: str) -> bool:
    try:
        return delete_run(run_id)
    except RunBusy:
        return False


def list_runs(*, include_archived: bool = False, limit: int | None = 30) -> list[dict]:
    """Recent runs, newest first — literal disk state only. Archived runs are hidden unless asked for."""
    base = runs_dir()
    if not base.exists():
        return []
    out = []
    for d in sorted(base.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not d.is_dir():
            continue
        archived = (d / ARCHIVED_MARK).exists()
        if archived and not include_archived:
            continue
        rows = _audit_rows(d)
        submit = next((r for r in rows if r.get("event") == "run_submit"), None)
        failed = next((r for r in rows if r.get("event") == "run_failed"), None)
        if submit is None and failed is None:
            continue
        states = {s: 0 for s in _STATES}
        if failed is not None:
            states["FAILED"] = 1
        for r in rows:
            if r.get("event") == "dispatch":
                st = _task_state(d, r["task"])
                states[st] = states.get(st, 0) + 1
        undispatched = (submit or {}).get("subtasks", 0) - sum(states.values())
        held = len(_held_steps(d, rows))
        states["AWAITING-APPROVAL"] += min(held, max(0, undispatched))
        states["QUEUED"] += max(0, undispatched - held)
        anchor = submit or failed
        out.append(
            {
                "run": d.name,
                "goal": anchor.get("goal", ""),
                "origin": anchor.get("origin", "cloud"),
                "device_id": anchor.get("device_id"),
                "subtasks": (submit or {}).get("subtasks", 0),
                "states": states,
                "ts": anchor.get("ts", d.stat().st_mtime),
                "archived": archived,
                "finished": _finished(states) and not _driving(d.name),
            }
        )
        if limit is not None and len(out) >= limit:
            break
    return out


def get_run(run_id: str) -> dict | None:
    """One run: goal, dispatch records, per-subtask state + results."""
    d = runs_dir() / run_id
    if not d.is_dir():
        return None
    rows = _audit_rows(d)
    submit = next((r for r in rows if r.get("event") == "run_submit"), None)
    failed = next((r for r in rows if r.get("event") == "run_failed"), None)
    fs = fs_adapter(d / "run")
    fails = [r for r in rows if r.get("event") == "worker_fail"]
    fails_by_task = {f.get("task"): f for f in fails}
    done_ts = {r.get("task"): r.get("ts") for r in rows if r.get("event") == "worker_done"}
    # A task can be claimed again after its lease expires; the latest claim is the attempt that finished.
    start_ts = {r.get("task"): r.get("ts") for r in rows if r.get("event") == "worker_start"}
    workers: dict[str, list[dict]] = {}  # every attempt that reported what served it, oldest first
    for r in rows:
        if isinstance(r.get("worker"), dict):
            workers.setdefault(r.get("task"), []).append(r["worker"])
    # The latest start/requeue row decides: a requeue after the last start means the step is waiting to run again.
    last_row: dict = {}
    for r in rows:
        if r.get("event") in ("worker_start", "worker_requeue"):
            last_row[r.get("task")] = r
    thoughts: dict[str, list[str]] = {}
    for t in sorted(ThinkingLog(d / "thinking.ndjson").rows(), key=lambda t: t.get("ts") or 0):
        key = PLAN if t.get("subtask") == PLAN else t.get("task")
        thoughts.setdefault(key, []).append(t["text"])
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
        if isinstance(r.get("router"), dict):
            rec["router"] = r["router"]
        if tid in workers:  # real spend: every attempt's worker cost plus what the router paid to decide
            rec["worker"] = workers[tid][-1]
            rec["cost"] = sum(w.get("cost") or 0.0 for w in workers[tid]) + (r.get("cost") or 0.0)
        if state == "DONE":
            try:
                rec["result"] = fs.collect("orchestrator", tid)
            except Exception:
                rec["state"] = "FAILED"  # tombstoned but unreadable = corrupted
        if tid in fails_by_task:
            f = fails_by_task[tid]
            rec["error"] = f.get("error", "")
            rec["error_kind"] = f.get("kind", "")
            rec["hint"] = f.get("hint", "")
            rec["finished_ts"] = f.get("ts")
            if f.get("kind") == "DependencyFailed":  # never started: no duration, shown as skipped
                rec["skipped"] = True
                rec["started_ts"] = f.get("ts")
        elif tid in done_ts:
            rec["finished_ts"] = done_ts[tid]
        rec["ts"] = r.get("ts")
        if tid in start_ts and not rec.get("skipped"):
            rec["started_ts"] = start_ts[tid]
        wait_row = last_row.get(tid)
        if wait_row and wait_row.get("event") == "worker_requeue" and state not in ("DONE", "FAILED"):
            rec["waiting_on"] = {
                "kind": wait_row.get("reason"),
                "attempt": wait_row.get("attempt"),
                "max": MAX_STEP_ATTEMPTS,
                "since": wait_row.get("ts"),
                "detail": wait_row.get("detail", ""),
            }
        if tid in thoughts:
            rec["thinking"] = join_thoughts(thoughts[tid])
        subs.append(rec)
    for h in _held_steps(d, rows):
        subs.append(
            {
                "subtask": h["sub"]["id"],
                "title": h["sub"]["title"],
                "state": "AWAITING-APPROVAL",
                "placement": "-",
                "reason": "waiting for approval to reach " + ", ".join(h["hosts"]),
                "approval": h["approval"],
                "hosts": h["hosts"],
            }
        )
    out = {
        "run": run_id,
        "goal": (submit or failed or {}).get("goal", ""),
        "origin": (submit or failed or {}).get("origin", "cloud"),
        "device_id": (submit or failed or {}).get("device_id"),
        "subtasks": subs,
        "declared": (submit or {}).get("subtasks", 0),
        "worker_failures": fails,
        "started": (submit or {}).get("ts"),
        "ended": rows[-1].get("ts") if rows else None,
    }
    if PLAN in thoughts:
        out["plan_thinking"] = join_thoughts(thoughts[PLAN])
    chosen = effort_row(rows)
    if chosen is not None:
        out["effort"] = _effort_view(d, run_id, chosen)
    if failed is not None:
        out["error"] = failed.get("error", "")
        out["error_kind"] = failed.get("kind", "")
        out["hint"] = failed.get("hint", "")
    return out


def _effort_view(run_dir: Path, run_id: str, row: dict) -> dict:
    """What the run was started with, plus live spend. `paused` means the budget stopped new work (nothing cancelled)."""
    view = {"id": row.get("effort"), "model": row.get("model"), "budget": row.get("budget"), "spent": 0.0, "paused": False}
    ledger = ledger_for(run_dir)
    if ledger is not None:
        st = ledger.state(run_id)
        view["spent"] = round(st.spend, 6)
        view["paused"] = st.state == "paused_cap"
    return view


def submit(
    goal: str,
    engine: Callable[[str], str],
    wfn: Callable[[dict], object],
    *,
    by: str = "",
    origin: str = "cloud",
    device_id: str | None = None,
    effort: str | None = None,
    model: str | None = None,
    budget: float | None = None,
) -> str:
    """Create a run dir, submit, and drive it on a daemon thread.

    With `budget`, the run gets a cost ledger whose hard cap pauses it (nothing cancelled) and an
    `effort_set` audit row recording the effort, model and budget it was started with."""
    rid = "goal_" + uuid.uuid4().hex[:8]
    run_dir = runs_dir() / rid
    run_dir.mkdir(parents=True, exist_ok=True)
    ledger = create_ledger(run_dir, rid, budget, by or "owner") if budget is not None else None
    orch = Orchestrator(run_dir, place_fn=place_live, ledger=ledger, egress=live_egress())
    if budget is not None:
        orch.audit.append({"event": "effort_set", "run": rid, "effort": effort, "model": model, "budget": budget})
    with _lock:
        _live[rid] = orch
    try:
        orch.submit(goal, engine, run_id=rid, origin=origin, device_id=device_id)
    except Exception:
        # run_failed audit row is already on disk — the run renders FAILED
        # with the real error; no driver, nothing to resume.
        return rid
    orch.audit.append({"event": "goal_by", "run": rid, "by": by})

    wf = durable.start_durable(run_dir, rid) if durable else None
    if wf:
        orch.audit.append({"event": "durable_start", "run": rid, "workflow": wf})
    else:
        _start_drive(orch, rid, wfn)
    return rid


def egress_wait_s() -> float:
    """How long a run waits on its up-front egress approval before the drive stops (a restart re-drives it)."""
    try:
        v = float(os.environ.get("OC_EGRESS_APPROVAL_WAIT_S", 86400))
    except ValueError:
        return 86400.0
    return v if 0 < v < float("inf") else 86400.0


def _drive(orch: Orchestrator, rid: str, wfn: Callable[[dict], object], sleep=time.sleep) -> None:
    try:
        for _ in range(400):
            res = orch.results(rid)
            if res and all(r["state"] in ("DONE", "FAILED") for r in res.values()):
                break
            if orch.work(wfn):
                continue
            wait = orch.retry_delay()
            if wait > 0:  # a step is backing off after a sandbox boot failure: wait here, not in the sweep
                sleep(wait)
            elif not orch.wait_for_egress(rid, timeout=egress_wait_s()):
                break
    except Exception:
        pass  # the audit rows + task files on disk remain the truth


def _start_drive(orch: Orchestrator, rid: str, wfn) -> None:
    threading.Thread(target=_drive, args=(orch, rid, wfn), daemon=True, name=f"oc-goal-{rid}").start()


def resume(wfn, wfn_for_model: Callable[[str], Callable[[dict], object]] | None = None) -> int:
    """Re-drive runs left non-terminal by a process restart. Count resumed.

    Orchestrator.recover rebuilds in-memory state from plan.json + audit
    rows; runs with no plan (pre-plan or mid-decompose crash) are left
    literal on disk — reported, never fabricated.

    A run started with an effort resumes with its own model (`wfn_for_model`) and its own cost ledger,
    so a restart never changes what it may spend.
    """
    base = runs_dir()
    if not base.exists():
        return 0
    n = 0
    for d in sorted(base.iterdir()):
        if not d.is_dir():
            continue
        try:
            orch = Orchestrator(d, place_fn=place_live, ledger=ledger_for(d), egress=live_egress())
            if not orch.recover(d.name):
                continue
            res = orch.results(d.name)
            if res and all(r["state"] in ("DONE", "FAILED") for r in res.values()):
                continue
            if (
                durable
                and durable.enabled()
                and any(r.get("event") == "durable_start" for r in _audit_rows(d))
                and durable.ensure_durable(d, d.name) is not None
            ):
                continue  # Temporal owns (or just re-adopted) the drive loop
            with _lock:
                _live[d.name] = orch
            model = model_of(_audit_rows(d))
            run_wfn = wfn_for_model(model) if wfn_for_model and model else wfn
            _start_drive(orch, d.name, run_wfn)
            n += 1
        except Exception:
            continue  # a corrupt run stays literal on disk
    return n
