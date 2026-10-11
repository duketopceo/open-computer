"""Approval console — the gate's human surface (TUI, first UX-bar surface).

Deterministic: render_request() is a pure function of the request. decide()
drives it through injectable input/output so tests never need a tty.
Keys: a = approve, r = reject, anything else re-prompts. No mouse paths.
"""

from __future__ import annotations

from typing import Callable

from .gate import Gate


def render_request(payload: dict, pii_categories: list[str]) -> str:
    """Exactly what would leave, rendered for review. Every state explicit."""
    lines = [
        "── escalation request ─────────────────────────",
        f"  model:    {payload.get('model', '<unset>')}",
        f"  egress:   {payload.get('egress', '<unset>')}",
        f"  payload:  {len(str(payload))} bytes",
        f"  pii:      {', '.join(pii_categories) if pii_categories else 'none detected'}",
        "───────────────────────────────────────────────",
        "  [a] approve this step   [r] reject",
    ]
    return "\n".join(lines)


def decide(
    gate: Gate,
    payload: dict,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> bool:
    """Scan → render → per-step keyboard decision → enforced call.

    Returns True only if the human approved AND the call was allowed.
    Every outcome writes an audit row (the gate handles that).
    """
    decision = gate.scan(str(payload))
    output_fn(render_request(payload, decision.categories))
    while True:
        key = input_fn("> ").strip().lower()
        if key in ("a", "r"):
            break
        output_fn("  (a) approve   (r) reject")
    approved = key == "a"
    gate.audit.append(
        {"event": "human_decision", "approved": approved, "model": payload.get("model")}
    )
    return gate.request_cloud_call(payload, approved=approved).allowed
