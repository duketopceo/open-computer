"""Dispatch — one subtask through route -> place -> fsproto mailbox (U6).

Every dispatch writes an audit row with the routing decision, the
placement target AND the placement reason — the UI's task view reads
these words, they are never inferred later.
"""

from __future__ import annotations

from typing import Callable

from services.device.parked import pick_device_for_task, required_capability
from services.device.protocol import seal
from services.placement import DEVICE_NEEDS, headroom_from
from services.telemetry import report_failure

PARKED = "device/parked"  # mailbox for device steps with no eligible device; drained on heartbeat (P1)


def device_mailbox(device_id: str) -> str:
    return f"device/{device_id}"


def _pick_device(devices, sub: dict, *, now: float | None = None):
    """First eligible device matching the step's capability, `device_id` pin, and `device_root`."""
    return pick_device_for_task(devices, sub, now=now)


def dispatch(fs, router, gate, place: Callable, audit, run_id: str, sub: dict, ledger=None, devices=None) -> dict:
    """Route, place, and offer a subtask to the worker inbox.

    `devices` is a services.device.DeviceRegistry (or None). A step the
    placer sends to `device` goes to that device's own mailbox; with no
    eligible device it is parked, and the audit row says so.

    Returns the dispatch record (also written to the audit log).
    """
    decision = router.route(sub["prompt"])
    pii = gate.scan(sub["prompt"])
    device = _pick_device(devices, sub) if devices is not None else None
    placement = place(
        {
            "tier": sub.get("tier", decision.tier),
            "latency": sub.get("latency", "normal"),
            "est_cost": sub.get("est_cost", 0.0),
            "needs_sandbox": sub.get("needs_sandbox", False),
            **{k: sub[k] for k in DEVICE_NEEDS if k in sub},
        },
        pii_flagged=bool(pii.categories),
        headroom=headroom_from(getattr(router, "budget", None), ledger, run_id),
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
        "placement": placement.target,
        "mailbox": dst,
        "reason": placement.reason,
        "gate_required": placement.gate_required,
        "pii": list(pii.categories),
    }
    audit.append(rec)
    if placement.target == "blocked":
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
        report_failure(
            f"NoPlacement: {placement.reason}",
            severity="warning",
            fingerprint="NoPlacement",
            run=run_id, task=tid, subtask=sub["id"],
        )
    return rec
