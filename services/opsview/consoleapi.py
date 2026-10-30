"""JSON payload builders for the React console pages (Sandboxes, Router, Audit).

Pure functions from already-read state to JSON-able dicts. No I/O, no spend:
the server reads state and passes it in, so the payloads are unit-testable and
a GET can never reach a mutation path through here. Rendering (and therefore
escaping) is the client's job; these return raw values.
"""

from __future__ import annotations

import json

AUDIT_FILTERS = ("all", "blocked", "allowed", "decisions")
AUDIT_LIMIT = 200
ROUTER_LEDGER_LIMIT = 14
PAID_ROUTE_COST = 0.000004  # approximate cost of one decider call; shown before it is sent


def audit_match(r: dict, f: str, q: str) -> bool:
    ev = str(r.get("event", ""))
    if f == "blocked" and "blocked" not in ev:
        return False
    if f == "allowed" and "allowed" not in ev:
        return False
    if f == "decisions" and ev != "human_decision":
        return False
    return not q or q.lower() in json.dumps(r).lower()


def audit_result(r: dict) -> str:
    ev = str(r.get("event", ""))
    if "blocked" in ev:
        return "blocked"
    if "allowed" in ev:
        return "allowed"
    if ev == "human_decision":
        return "approved" if r.get("approved") else "sent back"
    return "recorded"


def clean_audit_query(f: str, q: str) -> tuple[str, str]:
    """Same validation the old page applied: unknown filter -> all, text capped at 200."""
    return (f if f in AUDIT_FILTERS else "all"), q[:200]


def audit_payload(rows: list[dict], f: str, q: str) -> dict:
    """`rows` is the whole log, oldest first. Newest AUDIT_LIMIT matches are returned."""
    f, q = clean_audit_query(f, q)
    matched = [r for r in rows if audit_match(r, f, q)]
    shown = matched[-AUDIT_LIMIT:][::-1]
    return {
        "f": f,
        "q": q,
        "total": len(matched),
        "shown": len(shown),
        "rows": [
            {
                "ts": r.get("ts", 0),
                "event": str(r.get("event", "")),
                "result": audit_result(r),
                "detail": " ".join(f"{k}={v}" for k, v in r.items() if k not in ("ts", "event")),
            }
            for r in shown
        ],
    }


def sandboxes_payload(fleet: dict) -> dict:
    return {
        "configured": bool(fleet["configured"]),
        "up": bool(fleet["up"]),
        "api_host": fleet["api_host"],
        "sandboxes": fleet["sandboxes"],
    }


def router_payload(ledger: list[dict], spend: float, cap: float, paid: bool, dest_host: str) -> dict:
    """`ledger` is the whole ledger, oldest first; the newest few are returned newest first."""
    return {
        "paid": paid,
        "dest_host": dest_host,
        "per_route_cost": PAID_ROUTE_COST if paid else 0.0,
        "spend": spend,
        "cap": cap,
        "ledger": [
            {
                "ts": r.get("ts", 0),
                "event": str(r.get("event", "")),
                "route": str(r.get("route", "")),
                "engine": str(r.get("engine", "")),
                "cost": r.get("cost", 0) or 0,
            }
            for r in reversed(ledger[-ROUTER_LEDGER_LIMIT:])
        ],
    }
