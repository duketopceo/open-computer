"""Placement checks — the rule table's contract (U5).

PII never cloud-unapproved; over-budget, latency-crit and cheap tier
demote to local; frontier goes cloud through the gate. Targets are
compared literally — the same words the UI renders.
"""

from __future__ import annotations

from harness import CheckFailed, check


def _expect(p, target, gate=None) -> None:
    if p.target != target:
        raise CheckFailed(f"placed {p.target}, wanted {target} ({p.reason})")
    if gate is not None and p.gate_required != gate:
        raise CheckFailed(f"gate_required={p.gate_required}, wanted {gate}")


@check("PLACE", "PII-flagged subtask never lands on a cloud target")
def pii_never_cloud(place, tmp_path) -> None:
    p = place({"tier": "frontier", "latency": "normal"}, pii_flagged=True)
    if p.target.startswith("cloud"):
        raise CheckFailed(f"PII subtask placed {p.target} — must stay local")
    _expect(p, "local-ollama")


@check("PLACE", "over-budget subtask demotes to local")
def over_budget_goes_local(place, tmp_path) -> None:
    from services.router import Budget

    b = Budget(tmp_path / "ledger.jsonl", daily_cap=0.10, task_cap=0.10)
    b.record(0.09, "stub", "spend")
    p = place({"tier": "mid", "est_cost": 0.05}, budget=b)
    _expect(p, "local-ollama")
    if "over-budget" not in p.reason:
        raise CheckFailed(f"unexpected reason {p.reason!r}")


@check("PLACE", "latency-critical subtask stays local")
def latency_crit_goes_local(place, tmp_path) -> None:
    p = place({"tier": "mid", "latency": "crit"})
    _expect(p, "local-llamacpp")


@check("PLACE", "frontier-required subtask goes cloud and is gated")
def frontier_goes_cloud_gated(place, tmp_path) -> None:
    p = place({"tier": "frontier"})
    _expect(p, "cloud-OpenRouter", gate=True)


@check("PLACE", "approved PII may go cloud — gate still required")
def approved_pii_cloud_but_gated(place, tmp_path) -> None:
    p = place({"tier": "frontier"}, pii_flagged=True, approved=True)
    _expect(p, "cloud-OpenRouter", gate=True)
