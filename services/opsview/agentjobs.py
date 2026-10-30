"""Console model for agent-orchestrator jobs: the trail read model, the approval store, and the runner hook.

Reads never write (GET handlers use only list_jobs/get_job). The runner factory is injected with
`configure_runner`; unconfigured, a submitted job is recorded and reads `queued`.
"""

from __future__ import annotations

from pathlib import Path

from services.agents.approvals import ApprovalError, ApprovalStore
from services.agents.loop import DEFAULT_ORCHESTRATOR_MODEL
from services.agents.replay import replay
from services.agents.runner import Factory, JobRunner, resolve_budget
from services.agents.trail import Trail
from services.opsview import agentgrants
from services.opsview.goalruns import runs_dir

POLL_S = 2.0
_factory: Factory | None = None


def trail_path() -> Path:
    return runs_dir() / "agent-trail.ndjson"


def approvals_path() -> Path:
    return runs_dir() / "agent-approvals.ndjson"


def lease_dir() -> Path:
    return runs_dir() / "agent-leases"


def configure_runner(factory: Factory | None) -> None:
    global _factory
    _factory = factory


def _runner() -> JobRunner:
    return JobRunner(Trail(trail_path()), ApprovalStore(approvals_path()), lease_dir(), _factory, poll_s=POLL_S)


def resume_all() -> int:
    return _runner().resume_all()


def submit(goal: str, budget, by: str) -> str:
    return _runner().submit(goal, budget, by)


def submit_defaults(budget) -> dict:
    """What the confirm step shows. Raises ValueError for a budget that submit would refuse."""
    return {"budget": resolve_budget(budget), "model": DEFAULT_ORCHESTRATOR_MODEL}


_APPROVAL_KEYS = ("id", "kind", "detail", "hash", "ts", "expires_at")


def _summary(job: str, rows: list[dict], pending: list[dict]) -> dict:
    """One job's list row. `pending` is that job's undecided, unexpired requests (ignored once the job ended)."""
    st = replay(rows)
    meta = next((r for r in rows if r["event"] in ("job_submit", "job_start")), {})
    if st.outcome is not None:
        status, spent, pending = st.outcome.get("status"), st.outcome.get("spent"), []
    else:
        status = "running" if any(r["event"] == "job_start" for r in rows) else "queued"
        spent = round(st.spent, 4)
    return {"job": job, "goal": st.goal or meta.get("goal", ""), "started": meta.get("ts"), "status": status,
            "budget": st.budget, "spent": spent, "awaiting_approval": bool(pending), "pending": len(pending)}


def list_jobs() -> list[dict]:
    by_job: dict[str, list[dict]] = {}
    for r in Trail(trail_path()).rows():
        by_job.setdefault(r["job"], []).append(r)
    pend: dict[str, list[dict]] = {}
    for p in ApprovalStore(approvals_path()).pending():
        pend.setdefault(p["job"], []).append(p)
    return [_summary(j, rows, pend.get(j, [])) for j, rows in by_job.items()][::-1]


def get_job(job: str) -> dict | None:
    rows = Trail(trail_path()).rows(job)
    if not rows:
        return None
    pend = ApprovalStore(approvals_path()).pending(job)
    out = _summary(job, rows, pend)
    return {"job": job, "goal": out["goal"], "status": out["status"], "budget": out["budget"],
            "spent": out["spent"], "events": rows,
            "approvals": [{k: p[k] for k in _APPROVAL_KEYS} for p in pend] if out["awaiting_approval"] else []}


def decide(job: str, rid: str, approve: bool, by: str, hash_: object, note: str = "") -> None:
    """Record the owner's decision. Raises ApprovalError (404/409/422) or GrantError (500, request stays pending).
    An egress approval applies the grant inside the store's decision transaction, after the request is verified
    and before the approval is written: a deny, an expiry or a replay serialize with it, so no grant outlives a
    request that did not end approved."""
    store = ApprovalStore(approvals_path())
    r = store.get(rid)
    if r is None or r["job"] != job:
        raise ApprovalError(f"unknown approval {rid}", 404)
    grant = (lambda req: agentgrants.grant_host(req["detail"])) if approve and r["kind"] == "egress" else None
    store.decide(rid, approve, by, hash_, note, before_approve=grant)
