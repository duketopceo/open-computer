"""Job runner: submit a job, drive it on a daemon thread under a per-job lease, resume unfinished jobs at startup.

The planner and worker are injected through a factory `factory(job, goal, budget, model)` returning a dict:
{"planner", "worker", "cwd"} plus any other AgentLoop keyword (ledger, placement, max_rounds...). With no factory
a submitted job is only recorded (`job_submit`) and reads as queued. The trail is the state (replay.py): a job
with no `job_start` is started with `run`; one with `job_start` is continued with `resume`.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from oc.obs import get_logger

from .approvals import ApprovalStore, ConsoleApprover, lease
from .loop import DEFAULT_BUDGET, DEFAULT_ORCHESTRATOR_MODEL, AgentLoop
from .replay import replay
from .trail import Trail
from .worker import money

log = get_logger("agents")

RESUME_MAX_AGE_S = 86400.0
Factory = Callable[[str, str, float, str], dict]

_threads: dict[str, threading.Thread] = {}
_tlock = threading.Lock()


def max_budget() -> float:
    v = money(os.environ.get("OC_AGENT_MAX_BUDGET"), DEFAULT_BUDGET)
    return v if v > 0 else DEFAULT_BUDGET


def resume_max_age_s() -> float:
    v = money(os.environ.get("OC_AGENT_RESUME_MAX_AGE_S"), RESUME_MAX_AGE_S)
    return v if v > 0 else RESUME_MAX_AGE_S


def resolve_budget(raw) -> float:
    """None/"" -> the default; otherwise a positive amount within OC_AGENT_MAX_BUDGET, else ValueError."""
    if raw is None or raw == "":
        return DEFAULT_BUDGET
    v = money(raw)
    if v is None or v <= 0:
        raise ValueError("budget must be a positive number")
    if v > max_budget():
        raise ValueError(f"budget {v:g} is above the maximum {max_budget():g}")
    return v


def join_all(timeout: float = 5.0) -> None:
    """Wait for every driver thread (tests)."""
    deadline = time.monotonic() + timeout
    with _tlock:
        threads = list(_threads.values())
    for t in threads:
        t.join(max(0.0, deadline - time.monotonic()))


class JobRunner:
    def __init__(self, trail: Trail, store: ApprovalStore, lease_dir: Path | str, factory: Factory | None = None,
                 *, poll_s: float = 2.0, clock: Callable[[], float] = time.time):
        self.trail, self.store, self.lease_dir = trail, store, Path(lease_dir)
        self.factory, self.poll_s, self.clock = factory, poll_s, clock

    def _lease_path(self, job: str) -> Path:
        return self.lease_dir / f"{job}.lock"

    def submit(self, goal: str, budget, by: str) -> str:
        budget = resolve_budget(budget)  # refuse before anything is written
        job = uuid.uuid4().hex[:10]
        self.trail.append(job, "job_submit", goal=goal, budget=budget, model=DEFAULT_ORCHESTRATOR_MODEL, by=by)
        self._start(job)
        return job

    def _start(self, job: str) -> None:
        if self.factory is None:
            return  # queued: nothing runs, nothing is spent
        with _tlock:
            t = _threads.get(job)
            if t is not None and t.is_alive():
                return
            t = threading.Thread(target=self._drive, args=(job,), daemon=True, name=f"oc-agent-{job}")
            _threads[job] = t
        t.start()

    def _drive(self, job: str) -> None:
        try:
            with lease(self._lease_path(job)) as held:
                if not held:
                    return  # another process drives this job
                rows = self.trail.rows(job)
                st = replay(rows)
                if st.outcome is not None or not st.started:
                    return
                budget = st.budget or DEFAULT_BUDGET
                model = st.model or DEFAULT_ORCHESTRATOR_MODEL
                parts = dict(self.factory(job, st.goal, budget, model))
                loop = AgentLoop(self.trail, parts.pop("planner"), parts.pop("worker"),
                                 model=model, cwd=parts.pop("cwd"),
                                 approve=ConsoleApprover(self.store, job, poll_s=self.poll_s),
                                 budget=budget, **parts)
                if any(r.get("event") == "job_start" for r in rows):
                    # the resumed planner regenerates every round-level ask; only the plan approval is re-attached
                    self.store.void_unresolved(job)
                    loop.resume(job)
                else:
                    loop.run(st.goal, job=job)
        except Exception as e:  # noqa: BLE001 - a driver failure must not kill the console
            log.error("agent job driver failed", job=job, error=type(e).__name__)

    def resume_all(self) -> int:
        """Drive every unfinished, recent, unleased job. Needs a factory. Returns how many were started."""
        if self.factory is None:
            return 0
        now, max_age, n = self.clock(), resume_max_age_s(), 0
        by_job: dict[str, list[dict]] = {}
        for r in self.trail.rows():
            by_job.setdefault(r["job"], []).append(r)
        for job, rows in by_job.items():
            st = replay(rows)
            if st.outcome is not None or not st.started:
                continue
            if now - max((float(r.get("ts") or 0) for r in rows), default=0.0) > max_age:
                continue
            with lease(self._lease_path(job)) as held:
                if not held:
                    continue
            self._start(job)
            n += 1
        return n
