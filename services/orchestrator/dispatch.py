"""Dispatch — one subtask through route -> place -> fsproto mailbox (U6).

Every dispatch writes an audit row with the routing decision, the
placement target AND the placement reason — the UI's task view reads
these words, they are never inferred later.
"""

from __future__ import annotations

from typing import Callable

from oc.obs import devtrace, error_fields, get_logger
from services.device.parked import pick_device_for_task, required_capability
from services.device.protocol import seal
from services.placement import DEVICE_NEEDS, headroom_from, needs_device
from services.router.router import RouteDecision
from services.telemetry import report_failure

log = get_logger("orchestrator")
plog = get_logger("placement")

PARKED = "device/parked"  # mailbox for device steps with no eligible device; drained on heartbeat (P1)


def _router_view(d) -> dict:
    """The decision as the run page shows it. Tolerant of routers that return only route/tier/engine/cost."""
    return {"engine": d.engine, "model": getattr(d, "model_used", None) or getattr(d, "router_model", None),
            "confidence": getattr(d, "confidence", None), "ms": getattr(d, "ms", None),
            "fallbacks": getattr(d, "fallbacks", [])}


def device_mailbox(device_id: str) -> str:
    return f"device/{device_id}"


def _pick_device(devices, sub: dict, *, now: float | None = None):
    """First eligible device matching the step's capability, `device_id` pin, and `device_root`."""
    return pick_device_for_task(devices, sub, now=now)


def _trace_route(run_id: str, sub: dict, decision, placement) -> None:
    """Dev trace: the router's decision for a step, with the engines it skipped (U4 adds `fallbacks`)."""
    trace = devtrace.tracer(run_id=run_id)
    if trace is None:
        return
    try:
        fallbacks = []
        for f in getattr(decision, "fallbacks", None) or []:
            if isinstance(f, dict) and f.get("engine"):
                status = f.get("status")
                fallbacks.append({
                    "engine": str(f["engine"]),
                    "kind": str(f.get("kind") or ""),
                    "status": status if isinstance(status, int) and not isinstance(status, bool) else None,
                    "reason": devtrace.text(f.get("reason") or "", 300),
                })
        conf = decision.confidence
        trace.emit(
            "route",
            id=devtrace.entity("route", sub["id"]),
            step=devtrace.entity("step", sub["id"]),
            route=decision.route,
            tier=decision.tier,
            engine=decision.engine,
            model=decision.model_used,
            router_model=getattr(decision, "router_model", None),
            confidence=conf if isinstance(conf, (int, float)) and not isinstance(conf, bool) else None,
            cost_usd=max(0.0, float(decision.cost or 0.0)),
            ms=max(0.0, float(decision.ms or 0.0)),
            placement=placement.target,
            fallbacks=fallbacks or None,
        )
    except Exception as e:  # noqa: BLE001 - tracing never changes a dispatch
        log.warning("dev trace route dropped", **error_fields(e))


def _stored_decision(sub: dict, model: str | None) -> RouteDecision:
    """Goal steps use the stored tier and the effort model. They do not ask the router to classify the prompt."""
    return RouteDecision(
        "answer",
        str(sub.get("tier") or "mid"),
        "effort",
        None,
        0.0,
        0.0,
        model_used=model,
    )


def dispatch(fs, router, gate, place: Callable, audit, run_id: str, sub: dict, ledger=None, devices=None,
             chat_id: str | None = None, *, classify: bool = True, effort_model: str | None = None,
             pii_approved: bool = False, ledger_only: bool = False) -> dict:
    """Route, place, and offer a subtask to the worker inbox.

    `devices` is a services.device.DeviceRegistry (or None). A step the
    placer sends to `device` goes to that device's own mailbox; with no
    eligible device it is parked, and the audit row says so.

    Returns the dispatch record (also written to the audit log).
    """
    on_device = needs_device(sub)
    if on_device or not classify:
        decision = _stored_decision(sub, effort_model)
    else:
        decision = router.route(sub["prompt"])
    pii = gate.scan("" if on_device else sub["prompt"])
    device = _pick_device(devices, sub) if devices is not None else None
    # Goal steps judge headroom from the run ledger only. The router task cap is not this path.
    budget = None if ledger_only or not classify else getattr(router, "budget", None)
    headroom = headroom_from(budget, ledger, run_id)
    placement = place(
        {
            "tier": sub.get("tier", decision.tier),
            "latency": sub.get("latency", "normal"),
            "est_cost": sub.get("est_cost", 0.0),
            "needs_sandbox": sub.get("needs_sandbox", False),
            "needs_egress": sub.get("needs_egress", False),
            "egress_hosts": sub.get("egress_hosts") or [],
            **{k: sub[k] for k in DEVICE_NEEDS if k in sub},
        },
        pii_flagged=bool(pii.categories),
        approved=pii_approved,
        headroom=headroom,
        device_available=device is not None,
    )
    body = {
        "run": run_id,
        "subtask": sub["id"],
        "title": sub["title"],
        "prompt": sub["prompt"],
        "route": decision.route,
        "engine": decision.engine,
        "placement": placement.target,
        "est_cost": sub.get("est_cost", 0.0),
        **({"chat": chat_id} if chat_id else {}),
        **({"needs_web": True} if sub.get("needs_web") else {}),
        **({"needs_apps": True} if sub.get("needs_apps") else {}),
        **({"egress_hosts": list(placement.egress_hosts)} if placement.egress_hosts else {}),
        **{k: sub[k] for k in DEVICE_NEEDS if k in sub},
    }
    if placement.target == "device":
        cap = required_capability(sub)
        step = {
            "kind": sub.get("kind", "echo"),
            "text": sub["prompt"],
            "run": run_id,
            "subtask": sub["id"],
            **{k: sub[k] for k in DEVICE_NEEDS if k in sub},
            **({"capability": cap} if cap else {}),
        }
        body["step"] = step
        # The agent refuses unsigned work — only offer the task when we can seal for a
        # specific device; a device without a minted secret parks like an absent one.
        secret = devices.secret_for(device.device_id) if device is not None else None
        if device is not None and secret is not None:
            step["device_id"] = device.device_id
            body["envelope"] = seal(step, secret)
            dst = device_mailbox(device.device_id)
        else:
            dst = PARKED
    else:
        dst = "worker"
    tid = fs.send(dst, body)
    rec = {
        "event": "dispatch",
        "run": run_id,
        "task": tid,
        "subtask": sub["id"],
        "route": decision.route,
        "engine": decision.engine,
        "cost": decision.cost,
        "router": _router_view(decision),
        "placement": placement.target,
        "mailbox": dst,
        "reason": placement.reason,
        "gate_required": placement.gate_required,
        "pii": list(pii.categories),
    }
    audit.append(rec)
    _trace_route(run_id, sub, decision, placement)
    plog.info("placement decided", subtask=sub["id"], target=placement.target, reason=placement.reason,
              gate_required=placement.gate_required, headroom=headroom)
    if plog.enabled_debug():
        plog.debug("placement inputs", subtask=sub["id"], tier=sub.get("tier", decision.tier),
                   latency=sub.get("latency", "normal"), est_cost=sub.get("est_cost", 0.0),
                   needs_sandbox=sub.get("needs_sandbox", False), needs_egress=sub.get("needs_egress", False),
                   pii=list(pii.categories), device_available=device is not None)
    log.info("subtask dispatched", subtask=sub["id"], task=tid, route=decision.route, engine=decision.engine,
             placement=placement.target, mailbox=dst)
    if dst == PARKED:
        log.warning("subtask parked: no eligible device", subtask=sub["id"], task=tid)
    if placement.target == "blocked":
        plog.warning("placement blocked", subtask=sub["id"], task=tid, reason=placement.reason)
        # No legal placement exists (a demote-only rule with local down).
        # Tombstone like a dep-fail: on disk, failed, never leased — so the
        # run surfaces the placement reason instead of a worker exception.
        fs.fail(tid)
        audit.append(
            {
                "event": "worker_fail",
                "run": run_id,
                "task": tid,
                "subtask": sub["id"],
                "kind": "NoPlacement",
                "error": placement.reason,
                "hint": "raise the cap, approve the step, or restore local endpoints — then rerun",
            }
        )
        trace = devtrace.tracer(run_id=run_id)
        if trace is not None:
            trace.step(sub["id"], sub["title"], "failed", detail=devtrace.text(placement.reason, 300))
            trace.error(devtrace.entity("err", tid), "NoPlacement", placement.reason, sub["id"],
                        hint="raise the cap, approve the step, or restore local endpoints — then rerun")
        report_failure(
            f"NoPlacement: {placement.reason}",
            severity="warning",
            fingerprint="NoPlacement",
            run=run_id, task=tid, subtask=sub["id"],
        )
    return rec
