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


def place(
    subtask: dict,
    *,
    pii_flagged: bool = False,
    approved: bool = False,
    budget=None,
) -> Placement:
    """Where this subtask's model/execution lands.

    budget: a U4 Budget (or None). If present, a cloud call the remaining
    headroom can't cover demotes to local — spend caps fail closed. A
    corrupt ledger raises BudgetHalt from inside day_spend(); it
    propagates — halt, never silent-local.
    """
    latency = subtask.get("latency", "normal")
    tier = subtask.get("tier", "mid")
    model_req = subtask.get("model_requirement", "")
    est_cost = float(subtask.get("est_cost", 0.0))

    # 1. PII never reaches cloud without a recorded human approval.
    if pii_flagged and not approved:
        return Placement(
            _local_for(latency),
            "pii-flagged and unapproved — cloud targets closed",
        )

    # 2. Latency-critical work never waits on a network round trip.
    if latency == "crit":
        return Placement(
            "local-llamacpp", "latency=crit — GPU local, no network RTT"
        )

    # 3. Spend headroom: if the budget can't cover the estimate, local.
    if budget is not None and est_cost > 0:
        remaining = min(
            budget.task_cap - budget.task_spend,
            budget.daily_cap - budget.day_spend(),
        )
        if est_cost > remaining:
            return Placement(
                "local-ollama",
                f"over-budget — est {est_cost} > headroom {remaining:.4f}",
            )

    # 4. Work that must execute lands in a sandboxed worker.
    if subtask.get("needs_sandbox"):
        return Placement(
            "cloud-sandbox",
            "needs_sandbox — execution-capable worker",
            gate_required=pii_flagged,
        )

    # 5. Frontier-required tasks must go cloud — and the call records
    #    through the gate (R5).
    if tier == "frontier" or model_req == "frontier":
        return Placement(
            "cloud-OpenRouter",
            "frontier-required — cloud model, gated",
            gate_required=True,
        )

    # 6. Cheap tier has a competent local home — don't spend on it.
    if tier == "cheap":
        return Placement(
            "local-llamacpp", "cheap tier — local model suffices"
        )

    return Placement("cloud-OpenRouter", "default — cloud model")
