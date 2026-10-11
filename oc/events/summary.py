"""Server-side fold of a goal's events into the facts the goal list needs.

The UI has its own projection (static/projection.js); a browser test checks the two
agree on these fields so neither can drift.
"""

from __future__ import annotations


def fold(events: list[dict]) -> dict:
    s = {"id": None, "title": "", "status": "queued", "reason": None, "spent_usd": 0.0, "cap_usd": 0.0,
         "pending_approvals": 0, "artifacts": 0, "sources": 0, "changes": 0, "last_seq": 0, "updated": 0.0,
         "started": None}
    approvals: dict[str, str] = {}
    artifacts, sources, changes = set(), set(), set()
    for e in events:
        s["id"] = e["goal"]
        s["last_seq"], s["updated"] = e["seq"], e["ts"]
        d = e["data"]
        if e["type"] == "run":
            s["status"] = d["status"]
            s["reason"] = d.get("reason")
            if d["status"] not in ("waiting", "paused"):
                s["reason"] = None
            s["title"] = d.get("title", s["title"])
            if s["started"] is None:
                s["started"] = e["ts"]
            if "cap_usd" in d:
                s["cap_usd"] = d["cap_usd"]
        elif e["type"] == "cost":
            s["spent_usd"], s["cap_usd"] = d["spent_usd"], d["cap_usd"]
        elif e["type"] == "approval":
            approvals[d["id"]] = d["status"]
        elif e["type"] == "artifact":
            artifacts.add(d["id"])
        elif e["type"] == "source":
            sources.add(d["id"])
        elif e["type"] == "change":
            changes.add(d["id"])
    s["pending_approvals"] = sum(1 for v in approvals.values() if v == "pending")
    s.update(artifacts=len(artifacts), sources=len(sources), changes=len(changes))
    s["group"] = group(s)
    return s


def group(s: dict) -> str:
    """Left-column grouping: Needs you, Running, Done, Failed."""
    if s["status"] in ("failed", "cancelled"):
        return "failed"
    if s["pending_approvals"] or s["status"] in ("waiting", "paused"):
        return "needs_you"
    if s["status"] == "done":
        return "done"
    return "running"


def entities(events: list[dict], type_: str) -> dict[str, dict]:
    """Latest merged state per entity id for one event type (upsert semantics: later fields win)."""
    out: dict[str, dict] = {}
    for e in events:
        if e["type"] == type_:
            out[e["data"]["id"]] = {**out.get(e["data"]["id"], {}), **e["data"]}
    return out
