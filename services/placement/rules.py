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
  device             the user's own registered machine (services/device) —
                     the only target that can touch a granted local root,
                     local app or local browser. Local, never cloud.
  blocked            no legal placement exists — the step tombstones FAILED
                     with the reason instead of being sent to a dead engine.
                     Emitted when a rule can only demote to local but every
                     local endpoint is down.

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


def _local_for(latency: str) -> str:
    return "local-llamacpp" if latency == "crit" else "local-ollama"


def headroom_from(budget=None, ledger=None, run: str | None = None) -> float | None:
    """Spend headroom in dollars. None = nothing to enforce. This is the only impure step (it reads ledger
    files); place() itself takes the resulting number.

    A run with a cost ledger is capped by that ledger alone: the owner's effort budget is the per-run cap,
    and the router's small decision-spend `Budget` (a separate $0.10 task cap) must not shrink it, or a
    Medium or High run would quietly fall back to a local model long before its own budget is used.
    Without a ledger, the router's task and daily caps are the headroom."""
    if ledger is not None and run is not None:
        st = ledger.state(run)
        return 0.0 if st.cap is None or st.state != "running" else st.cap - st.spend - st.reserved
    if budget is not None:
        return min(budget.task_cap - budget.task_spend, budget.daily_cap - budget.day_spend())
    return None


def place(
    subtask: dict,
    *,
    pii_flagged: bool = False,
    approved: bool = False,
    headroom: float | None = None,
    budget=None,
    local_up: bool = True,
    local_enabled: bool = True,
    device_available: bool = True,
    egress_approved=(),
) -> Placement:
    """Where this subtask's model/execution lands. A pure function of its arguments.

    pii_flagged: the gate's scan of the prompt found PII.
    approved:    a human approval for exactly this payload exists. The gate re-verifies hash, expiry and
                 single use at send time, so True here only opens the *option* of a gated cloud call.
    local_enabled: the owner opted in to local models (`OC_LOCAL_MODELS`). When False nothing is ever placed
                 on a local endpoint, as if every one were down: cheap and latency-critical steps run on the
                 run's cloud model, and private or over-budget steps are held or blocked with a reason.
    headroom:    dollars still spendable (see headroom_from). A cloud call the headroom cannot cover
                 demotes to local. `budget` is a legacy shim: its headroom is derived here when `headroom`
                 is not given; a corrupt ledger raises BudgetHalt from day_spend() and propagates.

    Invariants (tests/unit/test_placement_table.py checks them over the whole cross product):
      I1 PII flagged and not approved -> never a cloud target.
      I2 latency=crit -> local, never cloud.
      I3 est_cost above headroom -> local, never cloud.
      I4 every cloud placement of PII-flagged content, and every frontier placement, has gate_required.
      I5 deterministic: same arguments, same Placement (reason included).
      I6 needs_device(subtask) -> target device, whatever else is set; nothing else ever lands on device.
    """
    latency = subtask.get("latency", "normal")
    tier = subtask.get("tier", "mid")
    model_req = subtask.get("model_requirement", "")
    est_cost = float(subtask.get("est_cost", 0.0))
    if headroom is None and budget is not None:
        headroom = headroom_from(budget=budget)
    if not local_enabled:
        local_up = False
    no_local = "local models are off" if not local_enabled else "local endpoints down"

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

    # 1. PII never reaches cloud without a recorded human approval. With
    #    local dead there is no legal home — block, don't crash-land the
    #    step on an endpoint that isn't there.
    if pii_flagged and not approved:
        if not local_enabled:
            return Placement(
                "blocked",
                "pii-flagged, unapproved, and local models are off — approve sending this step to the cloud model",
            )
        if not local_up:
            return Placement(
                "blocked",
                "pii-flagged, unapproved, and local endpoints down — approve the step or restore local",
            )
        return Placement(
            _local_for(latency),
            "pii-flagged and unapproved - cloud targets closed",
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

    # 3. Latency-critical work never waits on a network round trip — but a
    #    dead local surface demotes to cloud rather than fail the run.
    if latency == "crit":
        if not local_up:
            return Placement(
                "cloud-OpenRouter",
                f"latency=crit but {no_local} — cloud model",
            )
        return Placement("local-llamacpp", "latency=crit - GPU local, no network RTT")

    # 4. Spend headroom: if it cannot cover the estimate, local. With local
    #    dead the over-budget step has no legal home — block with the budget
    #    reason so the run fails on words, not a connection error.
    if headroom is not None and est_cost > 0 and est_cost > headroom:
        if not local_up:
            return Placement(
                "blocked",
                f"over-budget (est {est_cost} > headroom {headroom:.4f}) and {no_local} — raise the cap"
                + ("" if not local_enabled else " or restore local"),
            )
        return Placement(
            "local-ollama",
            f"over-budget - est {est_cost} > headroom {headroom:.4f}",
        )

    # 5. Frontier-required tasks must go cloud, and the call records through the gate (R5).
    if tier == "frontier" or model_req == "frontier":
        return Placement(
            "cloud-OpenRouter",
            "frontier-required - cloud model, gated",
            gate_required=True,
        )

    # 6. Cheap tier has a competent local home: do not spend on it. When
    #    no local endpoint is alive, a cheap task still deserves a result.
    if tier == "cheap":
        if not local_up:
            return Placement(
                "cloud-OpenRouter",
                f"cheap tier but {no_local} — cloud model",
            )
        return Placement("local-llamacpp", "cheap tier - local model suffices")

    return Placement(
        "cloud-OpenRouter",
        "default - cloud model" + (", approved PII, gated" if pii_flagged else ""),
        gate_required=pii_flagged,
    )
