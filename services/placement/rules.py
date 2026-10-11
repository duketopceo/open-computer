"""Placement policy — deterministic rule engine (U5).

subtask -> placement target. A table, not a model (per the verdict doc:
"a rule engine, not a model"). Rules are ordered first-match-wins, most
conservative first — PII and latency can only demote toward local, never
promote toward cloud.

Targets:
  local-ollama       CPU-class local model (:11434) — free, private, slow
  local-llamacpp     GPU llama.cpp (:8080/:8091) — free, private, fast
  cloud-OpenRouter   cloud model call via router — costs, needs gate when
                     sensitive or frontier
  cloud-sandbox      execution-capable sandboxed worker

Every Placement carries `reason` for the audit row — the matrix and the
UI read the same words, never inferred.
"""

from __future__ import annotations

from dataclasses import dataclass


TARGETS = (
    "local-ollama",
    "local-llamacpp",
    "cloud-OpenRouter",
    "cloud-sandbox",
)


@dataclass
class Placement:
    target: str
    reason: str
    gate_required: bool = False


def _local_for(latency: str) -> str:
    return "local-llamacpp" if latency == "crit" else "local-ollama"


def headroom_from(budget=None, ledger=None, run: str | None = None) -> float | None:
    """Spend headroom in dollars: the smallest remaining cap across the router's decision Budget and the
    run's cost ledger. None = nothing to enforce. This is the only impure step (it reads ledger files);
    place() itself takes the resulting number."""
    parts: list[float] = []
    if budget is not None:
        parts.append(min(budget.task_cap - budget.task_spend, budget.daily_cap - budget.day_spend()))
    if ledger is not None and run is not None:
        st = ledger.state(run)
        parts.append(0.0 if st.cap is None or st.state != "running" else st.cap - st.spend - st.reserved)
    return min(parts) if parts else None


def place(
    subtask: dict,
    *,
    pii_flagged: bool = False,
    approved: bool = False,
    headroom: float | None = None,
    budget=None,
) -> Placement:
    """Where this subtask's model/execution lands. A pure function of its arguments.

    pii_flagged: the gate's scan of the prompt found PII.
    approved:    a human approval for exactly this payload exists. The gate re-verifies hash, expiry and
                 single use at send time, so True here only opens the *option* of a gated cloud call.
    headroom:    dollars still spendable (see headroom_from). A cloud call the headroom cannot cover
                 demotes to local. `budget` is a legacy shim: its headroom is derived here when `headroom`
                 is not given; a corrupt ledger raises BudgetHalt from day_spend() and propagates.

    Invariants (tests/unit/test_placement_table.py checks them over the whole cross product):
      I1 PII flagged and not approved -> never a cloud target.
      I2 latency=crit -> local, never cloud.
      I3 est_cost above headroom -> local, never cloud.
      I4 every cloud placement of PII-flagged content, and every frontier placement, has gate_required.
      I5 deterministic: same arguments, same Placement (reason included).
    """
    latency = subtask.get("latency", "normal")
    tier = subtask.get("tier", "mid")
    model_req = subtask.get("model_requirement", "")
    est_cost = float(subtask.get("est_cost", 0.0))
    if headroom is None and budget is not None:
        headroom = headroom_from(budget=budget)

    # 1. PII never reaches cloud without a recorded human approval.
    if pii_flagged and not approved:
        return Placement(
            _local_for(latency),
            "pii-flagged and unapproved - cloud targets closed",
        )

    # 2. Latency-critical work never waits on a network round trip.
    if latency == "crit":
        return Placement(
            "local-llamacpp", "latency=crit - GPU local, no network RTT"
        )

    # 3. Spend headroom: if it cannot cover the estimate, local.
    if headroom is not None and est_cost > 0 and est_cost > headroom:
        return Placement(
            "local-ollama",
            f"over-budget - est {est_cost} > headroom {headroom:.4f}",
        )

    # 4. Work that must execute lands in a sandboxed worker.
    if subtask.get("needs_sandbox"):
        return Placement(
            "cloud-sandbox",
            "needs_sandbox - execution-capable worker",
            gate_required=pii_flagged,
        )

    # 5. Frontier-required tasks must go cloud, and the call records through the gate (R5).
    if tier == "frontier" or model_req == "frontier":
        return Placement(
            "cloud-OpenRouter",
            "frontier-required - cloud model, gated",
            gate_required=True,
        )

    # 6. Cheap tier has a competent local home: do not spend on it.
    if tier == "cheap":
        return Placement(
            "local-llamacpp", "cheap tier - local model suffices"
        )

    return Placement(
        "cloud-OpenRouter",
        "default - cloud model" + (", approved PII, gated" if pii_flagged else ""),
        gate_required=pii_flagged,
    )
