"""Console read model for agent-orchestrator jobs (read-only view over services.agents.trail)."""

from __future__ import annotations

from pathlib import Path

from services.agents.trail import Trail
from services.opsview.goalruns import runs_dir


def trail_path() -> Path:
    return runs_dir() / "agent-trail.ndjson"


def list_jobs() -> list[dict]:
    t = Trail(trail_path())
    out = []
    for job in t.jobs():
        rows = t.rows(job)
        start = next((r for r in rows if r["event"] == "job_start"), {})
        end = next((r for r in reversed(rows) if r["event"] == "job_end"), None)
        pending = [r for r in rows if r["event"] == "approval_request"]
        decided = [r for r in rows if r["event"] == "approval"]
        out.append({
            "job": job, "goal": start.get("goal", ""), "started": start.get("ts"),
            "status": end["status"] if end else "running", "budget": start.get("budget"),
            "spent": end.get("spent") if end else sum(r.get("cost", 0) for r in rows if r["event"] == "result"),
            "awaiting_approval": len(pending) > len(decided) and end is None,
        })
    return out[::-1]


def get_job(job: str) -> dict | None:
    rows = Trail(trail_path()).rows(job)
    return {"job": job, "events": rows} if rows else None
