"""Orchestrator glue — decompose -> dispatch -> collect (U6).

Composes U1 (fsproto), U3 (gate), U4 (router), U5 (placement). The
engine for decomposition and the work_fn for execution are injectable;
conformance drives them with stubs.

Conformance: tests/conformance/checks/orchestrator_checks.py (ORCH rows).
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from oc.fsproto import adapter as fs_adapter
from oc.obs import bind, error_fields, get_logger
from services.device.parked import drain_parked
from services.gate import adapter as gate_adapter
from services.placement import place
from services.gate.audit import AuditLog
from services.openrouter import collect_reasoning, collect_usage, total_usage
from services.router import adapter as router_adapter

from .collect import collect_run, task_state
from .decompose import DecomposeError, decompose
from .dispatch import dispatch
from .runner import WorkResult, run_once
from .thinking import PLAN, ThinkingLog, join_thoughts

log = get_logger("orchestrator")

__all__ = [
    "DecomposeError",
    "Orchestrator",
    "adapter",
    "collect_run",
    "decompose",
    "dispatch",
    "drain_parked",
    "WorkResult",
    "run_once",
    "task_state",
]


class Orchestrator:
    """goal -> dispatched subtask DAG -> collected results."""

    def __init__(self, root: Path | str, router=None, place_fn=None, ledger=None, devices=None):
        root = Path(root)
        self.root = root
        self.fs = fs_adapter(root / "run")
        self.gate = gate_adapter(root / "gate")
        self.audit = AuditLog(root / "orchestrator.ndjson")
        self.thinking = ThinkingLog(root / "thinking.ndjson")
        self.router = router or router_adapter(root)
        self.place = place_fn or place
        self.ledger = ledger  # services.cost.CostLedger | None
        self.devices = devices  # services.device.DeviceRegistry | None (P1)
        if self.devices is not None and hasattr(self.devices, "bind"):
            self.devices.bind(fs=self.fs, audit=self.audit)
        self.dispatches: dict[str, list[dict]] = {}  # run_id -> records
        self._subtasks: dict[str, dict[str, dict]] = {}  # run_id -> id -> sub
        self._tid_by_sub: dict[str, dict[str, str]] = {}  # run_id -> sid -> tid
        self._run_meta: dict[str, dict] = {}  # run_id -> {"origin": ..., "device_id": ...}
        self._chat: dict[str, str] = {}  # run_id -> chat id (OpenRouter session / per-chat cost views)

    def drain_parked(self, *, now: float | None = None) -> list[dict]:
        """Drain tasks queued in `device/parked` into eligible `device/<device_id>` mailboxes."""
        drained = drain_parked(self.fs, self.devices, self.audit, now=now)
        if drained:
            by_task = {d["task"]: d["mailbox"] for d in drained}
            for recs in self.dispatches.values():
                for rec in recs:
                    if rec.get("task") in by_task:
                        rec["mailbox"] = by_task[rec["task"]]
        return drained

    def run_meta(self, run_id: str) -> dict:
        """Return `{origin, device_id}` metadata for `run_id`."""
        return dict(self._run_meta.get(run_id, {"origin": "cloud", "device_id": None}))

    def submit(
        self,
        goal: str,
        engine,
        run_id: str | None = None,
        *,
        origin: str = "cloud",
        device_id: str | None = None,
        chat_id: str | None = None,
    ) -> str:
        """Decompose + validate + dispatch every ready subtask. Returns run id.

        `chat_id` groups runs into one conversation for cost attribution (defaults to the run id)."""
        run_id = run_id or uuid.uuid4().hex[:12]
        self._run_meta[run_id] = {"origin": origin, "device_id": device_id}
        if chat_id:
            self._chat[run_id] = str(chat_id)
        with bind(run_id=run_id, chat_id=chat_id, service="orchestrator"):
            return self._submit(goal, engine, run_id, origin, device_id)

    def _submit(self, goal: str, engine, run_id: str, origin: str, device_id: str | None) -> str:
        try:
            with collect_usage() as used, collect_reasoning() as thoughts:
                try:
                    subs = decompose(goal, engine)
                finally:
                    if thoughts:
                        self.thinking.append(run_id, "", PLAN, join_thoughts(thoughts))
            planned = total_usage(used)
            if planned is not None and self.ledger is not None:
                self.ledger.record(run_id, "_plan", planned)  # planner spend counts against the run's cap
        except Exception as e:
            fail_row: dict = {
                "event": "run_failed",
                "run": run_id,
                "goal": goal,
                "origin": origin,
                "kind": type(e).__name__,
                "error": str(e),
                "hint": getattr(e, "hint", ""),
            }
            if device_id is not None:
                fail_row["device_id"] = device_id
            self.audit.append(fail_row)
            log.error("run failed before dispatch", origin=origin, **error_fields(e))
            raise
        self.dispatches[run_id] = []
        self._subtasks[run_id] = {s["id"]: s for s in subs}
        self._tid_by_sub[run_id] = {}
        submit_row: dict = {
            "event": "run_submit",
            "run": run_id,
            "goal": goal,
            "subtasks": len(subs),
            "origin": origin,
        }
        if device_id is not None:
            submit_row["device_id"] = device_id
        if run_id in self._chat:
            submit_row["chat"] = self._chat[run_id]
        log.info("run submitted", goal_chars=len(goal), subtasks=len(subs), origin=origin,
                 planner_cost=getattr(planned, "cost", None))
        if log.enabled_debug():
            log.debug("plan", subtasks=[{"id": s["id"], "deps": s["deps"], "tier": s["tier"],
                                         "latency": s["latency"], "est_cost": s["est_cost"]} for s in subs])
        self.audit.append(submit_row)
        tmp = self.root / "plan.json.tmp"
        tmp.write_text(json.dumps(subs))
        os.replace(tmp, self.root / "plan.json")
        self.dispatch_ready(run_id)
        return run_id

    def recover(self, run_id: str) -> bool:
        """Rebuild run state from disk so a restarted process can re-drive it.

        Reads plan.json (written atomically at submit) plus the dispatch
        audit rows. Returns False when no plan exists — a pre-plan or
        mid-decompose run is left literal on disk, never fabricated.
        """
        plan = self.root / "plan.json"
        if not plan.exists():
            log.info("recover skipped: no plan on disk", run_id=run_id)
            return False
        subs = json.loads(plan.read_text())
        self.dispatches[run_id] = []
        self._subtasks[run_id] = {s["id"]: s for s in subs}
        self._tid_by_sub[run_id] = {}
        self._run_meta.setdefault(run_id, {"origin": "cloud", "device_id": None})
        for r in self.audit.rows():
            if r.get("event") == "run_submit" and r.get("run") == run_id:
                self._run_meta[run_id] = {
                    "origin": r.get("origin", "cloud"),
                    "device_id": r.get("device_id"),
                }
                if r.get("chat"):
                    self._chat[run_id] = r["chat"]
            elif r.get("event") == "dispatch" and r.get("run") == run_id:
                self.dispatches[run_id].append(r)
                self._tid_by_sub[run_id][r["subtask"]] = r["task"]
        with bind(run_id=run_id, chat_id=self._chat.get(run_id), service="orchestrator"):
            log.info("run recovered", subtasks=len(subs), dispatched=len(self.dispatches[run_id]))
        return True

    def _dep_state(self, tid: str) -> str:
        status = self.fs._task_dir(tid) / "status"
        return status.read_text().strip() if status.exists() else "queued"

    def _with_dep_results(self, sub: dict, tids: dict) -> dict:
        """Append each done dep's result to the step prompt — downstream
        workers see real outputs (paths, counts) instead of guessing."""
        chunks = []
        for d in sub["deps"]:
            try:
                chunks.append(f"[{d} result]\n{self.fs.collect('orchestrator', tids[d])[:1500]}")
            except Exception:
                continue
        if not chunks:
            return sub
        return {**sub, "prompt": sub["prompt"] + "\n\nPrior step outputs:\n" + "\n\n".join(chunks)}

    def _dep_fail(self, run_id: str, sub: dict, blocked_by: str) -> dict:
        """Dep FAILED: dispatch a tombstone task and fail it before any lease.

        The task is a literal on-disk record (status=failed, never worked);
        deeper dependents cascade through it in the same dispatch_ready call.
        """
        tid = self.fs.send(
            "worker",
            {
                "run": run_id,
                "subtask": sub["id"],
                "title": sub["title"],
                "prompt": sub["prompt"],
                "blocked_by": blocked_by,
            },
        )
        self.fs.fail(tid)
        reason = f"dep '{blocked_by}' failed — cannot run"
        log.warning("dep failed: tombstoned dependent", subtask=sub["id"], blocked_by=blocked_by, task=tid)
        rec = {
            "event": "dispatch",
            "run": run_id,
            "task": tid,
            "subtask": sub["id"],
            "route": "-",
            "engine": "-",
            "placement": "-",
            "reason": reason,
            "gate_required": False,
            "pii": list(sub["pii"]) if isinstance(sub.get("pii"), list) else [],
        }
        self.audit.append(rec)
        self.audit.append(
            {
                "event": "worker_fail",
                "run": run_id,
                "task": tid,
                "subtask": sub["id"],
                "kind": "DependencyFailed",
                "error": reason,
                "hint": "rerun the goal, or fix the failed dep and resume the run",
            }
        )
        return rec

    def dispatch_ready(self, run_id: str) -> list[dict]:
        """Dispatch ready subtasks; dep-fail ones whose deps can't complete.

        A FAILED dep never satisfies fs.done, so dependents would queue
        forever — they get a pre-failed tombstone task instead, cascading
        through deeper dependents via fixpoint (bounded by subtask count).
        """
        with bind(run_id=run_id, chat_id=self._chat.get(run_id), service="orchestrator"):
            return self._dispatch_ready(run_id)

    def _dispatch_ready(self, run_id: str) -> list[dict]:
        subs = self._subtasks[run_id]
        sent = self.dispatches[run_id]
        tids = self._tid_by_sub[run_id]
        out = []
        progressed = True
        while progressed:
            progressed = False
            for sid, sub in subs.items():
                if sid in tids or any(d not in tids for d in sub["deps"]):
                    continue  # dispatched already, or dep not dispatched yet
                states = [self._dep_state(tids[d]) for d in sub["deps"]]
                if all(st == "done" for st in states):
                    rec = dispatch(
                        self.fs,
                        self.router,
                        self.gate,
                        self.place,
                        self.audit,
                        run_id,
                        self._with_dep_results(sub, tids),
                        ledger=self.ledger,
                        devices=self.devices,
                        chat_id=self._chat.get(run_id),
                    )
                elif any(st == "failed" for st in states):
                    rec = self._dep_fail(run_id, sub, sub["deps"][states.index("failed")])
                else:
                    continue  # dep still queued/leased — wait for it
                log.debug("dispatched", subtask=sid, task=rec["task"], placement=rec["placement"],
                          deps=len(sub["deps"]))
                tids[sid] = rec["task"]
                sent.append(rec)
                out.append(rec)
                progressed = True
        return out

    def work(self, work_fn, agent: str = "worker", ttl: float = 60) -> list[str]:
        """One worker sweep, then dispatch the newly-unblocked wave."""
        done = run_once(self.fs, self.audit, agent, work_fn, ttl=ttl, ledger=self.ledger, thinking=self.thinking)
        for run_id in self.dispatches:
            self.dispatch_ready(run_id)
        return done

    def results(self, run_id: str) -> dict:
        return collect_run(self.fs, self.dispatches[run_id], list(self._subtasks[run_id]))

    def run(
        self,
        goal: str,
        engine,
        work_fn,
        agent: str = "worker",
        *,
        origin: str = "cloud",
        device_id: str | None = None,
    ) -> dict:
        """E2E: submit, then work/dispatch until no task is left pending."""
        run_id = self.submit(goal, engine, origin=origin, device_id=device_id)
        for _ in range(64):
            before = self.results(run_id)
            if all(r["state"] in ("DONE", "FAILED") for r in before.values()):
                break
            if not self.work(work_fn, agent):
                break
        out = self.results(run_id)
        with bind(run_id=run_id, chat_id=self._chat.get(run_id), service="orchestrator"):
            states: dict[str, int] = {}
            for r in out.values():
                states[r["state"]] = states.get(r["state"], 0) + 1
            (log.info if states.get("FAILED", 0) == 0 and states.get("QUEUED", 0) == 0 else log.warning)(
                "run settled", states=states, subtasks=len(out))
        return out


def adapter(root: Path | str, **kw) -> Orchestrator:
    return Orchestrator(root, **kw)
