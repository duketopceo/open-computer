"""Dispatch — one subtask through route -> place -> fsproto mailbox (U6).

Every dispatch writes an audit row with the routing decision, the
placement target AND the placement reason — the UI's task view reads
these words, they are never inferred later.
"""

from __future__ import annotations

from typing import Callable

from services.placement import headroom_from


def dispatch(fs, router, gate, place: Callable, audit, run_id: str, sub: dict, ledger=None) -> dict:
    """Route, place, and offer a subtask to the worker inbox.

    Returns the dispatch record (also written to the audit log).
    """
    decision = router.route(sub["prompt"])
    pii = gate.scan(sub["prompt"])
    placement = place(
        {
            "tier": sub.get("tier", decision.tier),
            "latency": sub.get("latency", "normal"),
            "est_cost": sub.get("est_cost", 0.0),
            "needs_sandbox": sub.get("needs_sandbox", False),
        },
        pii_flagged=bool(pii.categories),
        headroom=headroom_from(getattr(router, "budget", None), ledger, run_id),
    )
    tid = fs.send(
        "worker",
        {
            "run": run_id,
            "subtask": sub["id"],
            "title": sub["title"],
            "prompt": sub["prompt"],
            "route": decision.route,
            "engine": decision.engine,
            "placement": placement.target,
            "est_cost": sub.get("est_cost", 0.0),
        },
    )
    rec = {
        "event": "dispatch",
        "run": run_id,
        "task": tid,
        "subtask": sub["id"],
        "route": decision.route,
        "engine": decision.engine,
        "cost": decision.cost,
        "placement": placement.target,
        "reason": placement.reason,
        "gate_required": placement.gate_required,
        "pii": list(pii.categories),
    }
    audit.append(rec)
    return rec
