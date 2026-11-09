"""Placement policy — deterministic rule engine (U5).

subtask -> placement target. A table, not a model (per the verdict doc:
"a rule engine, not a model"). Rules are ordered first-match-wins, most
conservative first.

Every model call goes through OpenRouter, with OpenRouter's own model
fallbacks after the effort's primary model. There is no local-model target:
a step that cannot go to the cloud model (unapproved PII, over budget) blocks
with the reason instead.

Targets:
  cloud-OpenRouter   cloud model call via router — costs, needs gate when
                     sensitive or frontier
  cloud-sandbox      execution-capable sandboxed worker
  device             the user's own registered machine (services/device) —
                     the only target that can touch a granted local root,
                     local app or local browser. Local, never cloud.
  blocked            no legal placement exists — the step tombstones FAILED
                     with the reason (unapproved PII, over budget, egress
                     nobody approved).

Every Placement carries `reason` for the audit row — the matrix and the
UI read the same words, never inferred.
"""

from __future__ import annotations

from dataclasses import dataclass


TARGETS = (
    "cloud-OpenRouter",
    "cloud-sandbox",
    "device",
    "blocked",
)

DEVICE_NEEDS = ("device_root", "needs_local_app", "needs_local_browser")


def needs_device(subtask: dict) -> bool:
    """A step that must run on the user's machine: it names a granted root, a local app or the local browser."""
    return any(subtask.get(k) for k in DEVICE_NEEDS)


@dataclass
class Placement:
    target: str
    reason: str
    gate_required: bool = False
    egress_hosts: tuple[str, ...] = ()  # S5: approved hosts the sandbox may reach


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
    device_available: bool = True,
    egress_approved=(),
) -> Placement:
    """Where this subtask's model/execution lands. A pure function of its arguments.

    pii_flagged: the gate's scan of the prompt found PII.
    approved:    a human approval for exactly this payload exists. The gate re-verifies hash, expiry and
                 single use at send time, so True here only opens the *option* of a gated cloud call.
    headroom:    dollars still spendable (see headroom_from). A cloud call the headroom cannot cover
                 blocks. `budget` is a legacy shim: its headroom is derived here when `headroom`
                 is not given; a corrupt ledger raises BudgetHalt from day_spend() and propagates.

    Invariants (tests/unit/test_placement_table.py checks them over the whole cross product):
      I0 no placement is ever a local model: every model call is cloud-OpenRouter (or the sandbox/device/blocked).
      I1 PII flagged and not approved -> never a cloud target.
      I3 est_cost above headroom -> never a cloud model call.
      I4 every cloud placement of PII-flagged content, and every frontier placement, has gate_required.
      I5 deterministic: same arguments, same Placement (reason included).
      I6 needs_device(subtask) -> target device, whatever else is set; nothing else ever lands on device.
    """
    tier = subtask.get("tier", "mid")
    model_req = subtask.get("model_requirement", "")
    est_cost = float(subtask.get("est_cost", 0.0))
    if headroom is None and budget is not None:
        headroom = headroom_from(budget=budget)

    # 0. Steps bound to the user's machine run there or nowhere: a local
    #     file, app or browser cannot be reached from any other target.
    #     Device is local, so PII and spend rules are satisfied by it.
    if needs_device(subtask):
        if not device_available:
            return Placement(
                "device",
                "device-required but no eligible device - run parks until a heartbeat arrives",
            )
        return Placement("device", "device-required - runs on the user's registered machine")

    # 1. PII never reaches cloud without a recorded human approval, and
    #    there is no local model to send it to: block with the reason.
    if pii_flagged and not approved:
        return Placement(
            "blocked",
            "has private details and no approval — approve the step to send it to the cloud model",
        )

    # 1.5 Egress: a step that needs the network can only run where human-
    #     approved egress exists (S5). A named host nobody approved — or no
    #     approved host at all — blocks on words, not rc=1 in a deny-all VM.
    if subtask.get("needs_egress"):
        requested = [h for h in (subtask.get("egress_hosts") or []) if h]
        unapproved = [h for h in requested if h not in egress_approved]
        if unapproved:
            return Placement(
                "blocked",
                f"needs egress to {unapproved} — hosts not approved by a human",
            )
        effective = tuple(requested or sorted(egress_approved))
        if not effective:
            return Placement(
                "blocked",
                "needs_egress — no approved egress for this step",
            )
        return Placement(
            "cloud-sandbox",
            f"needs_egress — approved hosts: {', '.join(effective)}",
            gate_required=pii_flagged,
            egress_hosts=effective,
        )

    # 2. Work that must execute lands in a sandboxed worker — capability
    #    beats latency: a chat endpoint cannot run code. The spend cap
    #    still binds, but with no executable fallback its failure mode is
    #    `blocked`, never a demotion to a surface that cannot run it.
    if subtask.get("needs_sandbox"):
        if headroom is not None and est_cost > 0 and est_cost > headroom:
            return Placement(
                "blocked",
                f"over-budget (est {est_cost} > headroom {headroom:.4f}) and only a sandbox can run this step — raise the cap",
            )
        return Placement(
            "cloud-sandbox",
            "needs_sandbox - execution-capable worker",
            gate_required=pii_flagged,
        )

    # 3. Spend headroom: a cloud call it cannot cover blocks with the budget
    #    reason. (A run paused at its cap never gets here: the orchestrator
    #    holds its steps until the owner resumes.)
    if headroom is not None and est_cost > 0 and est_cost > headroom:
        return Placement(
            "blocked",
            f"over-budget (est {est_cost} > headroom {headroom:.4f}) — raise the cap",
        )

    # 4. Frontier-required tasks must go cloud, and the call records through the gate (R5).
    if tier == "frontier" or model_req == "frontier":
        return Placement(
            "cloud-OpenRouter",
            "frontier-required - cloud model, gated",
            gate_required=True,
        )

    return Placement(
        "cloud-OpenRouter",
        "cloud model via OpenRouter" + (", approved PII, gated" if pii_flagged else ""),
        gate_required=pii_flagged,
    )
