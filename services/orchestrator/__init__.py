"""Orchestrator glue — decompose -> dispatch -> collect (U6).

Composes U1 (fsproto), U3 (gate), U4 (router), U5 (placement). The
engine for decomposition and the work_fn for execution are injectable;
conformance drives them with stubs.

Conformance: tests/conformance/checks/orchestrator_checks.py (ORCH rows).
"""

from __future__ import annotations

import uuid
from pathlib import Path

from oc.fsproto import adapter as fs_adapter
from services.gate import adapter as gate_adapter
from services.placement import place
from services.gate.audit import AuditLog
from services.router import adapter as router_adapter

from .collect import collect_run, task_state
from .decompose import DecomposeError, decompose
from .dispatch import dispatch
from .runner import WorkResult, run_once

__all__ = [
    "DecomposeError",
    "Orchestrator",
    "adapter",
    "collect_run",
    "decompose",
    "dispatch",
    "WorkResult",
    "run_once",
    "task_state",
]


class Orchestrator:
    """goal -> dispatched subtask DAG -> collected results."""

    def __init__(self, root: Path | str, router=None, place_fn=None, ledger=None):
        root = Path(root)
        self.fs = fs_adapter(root / "run")
        self.gate = gate_adapter(root / "gate")
        self.audit = AuditLog(root / "orchestrator.ndjson")
        self.router = router or router_adapter(root)
        self.place = place_fn or place
        self.ledger = ledger  # services.cost.CostLedger | None
        self.dispatches: dict[str, list[dict]] = {}  # run_id -> records
        self._subtasks: dict[str, dict[str, dict]] = {}  # run_id -> id -> sub
        self._tid_by_sub: dict[str, dict[str, str]] = {}  # run_id -> sid -> tid

    def submit(self, goal: str, engine) -> str:
        """Decompose + validate + dispatch every ready subtask. Returns run id."""
        run_id = uuid.uuid4().hex[:12]
        subs = decompose(goal, engine)
        self.dispatches[run_id] = []
        self._subtasks[run_id] = {s["id"]: s for s in subs}
        self._tid_by_sub[run_id] = {}
        self.audit.append(
            {"event": "run_submit", "run": run_id, "goal": goal, "subtasks": len(subs)}
        )
        self.dispatch_ready(run_id)
        return run_id

    def dispatch_ready(self, run_id: str) -> list[dict]:
        """Dispatch every subtask whose deps are all tombstoned."""
        subs = self._subtasks[run_id]
        sent = self.dispatches[run_id]
        tids = self._tid_by_sub[run_id]
        out = []
        for sid, sub in subs.items():
            if sid in tids:
                continue  # already dispatched
            if all(d in tids and self.fs.done(tids[d]) for d in sub["deps"]):
                rec = dispatch(
                    self.fs, self.router, self.gate, self.place, self.audit, run_id, sub
                )
                tids[sid] = rec["task"]
                sent.append(rec)
                out.append(rec)
        return out

    def work(self, work_fn, agent: str = "worker", ttl: float = 60) -> list[str]:
        """One worker sweep, then dispatch the newly-unblocked wave."""
        done = run_once(self.fs, self.audit, agent, work_fn, ttl=ttl, ledger=self.ledger)
        for run_id in self.dispatches:
            self.dispatch_ready(run_id)
        return done

    def results(self, run_id: str) -> dict:
        return collect_run(
            self.fs, self.dispatches[run_id], list(self._subtasks[run_id])
        )

    def run(self, goal: str, engine, work_fn, agent: str = "worker") -> dict:
        """E2E: submit, then work/dispatch until no task is left pending."""
        run_id = self.submit(goal, engine)
        for _ in range(64):
            before = self.results(run_id)
            if all(r["state"] in ("DONE", "FAILED") for r in before.values()):
                break
            if not self.work(work_fn, agent):
                break
        return self.results(run_id)


def adapter(root: Path | str, **kw) -> Orchestrator:
    return Orchestrator(root, **kw)
