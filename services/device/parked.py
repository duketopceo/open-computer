"""Parked device mailbox drain — moves queued tasks from `device/parked` to `device/<id>` (plan P5).

When a step requiring a user device is dispatched while no device is eligible,
`services/orchestrator/dispatch.py` places the task in mailbox `device/parked`.
When an eligible device registers or heartbeats, `drain_parked(fs, registry, audit)`
moves pending tasks from `device/parked` into `device/<device_id>` and appends a
`parked_drained` audit event for each moved task.

Parked tasks carry an unsigned `step` — the drain seals it for the device that
actually picks the work up (a parked body can't name its device in advance).
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

from oc.fsproto import _parse_record, _write_record

from .protocol import seal

if TYPE_CHECKING:
    from oc.fsproto import FSAdapter
    from services.device.registry import Device, DeviceRegistry

PARKED_MAILBOX = "device/parked"


def required_capability(task_msg: dict[str, Any]) -> str | None:
    if task_msg.get("capability"):
        return str(task_msg["capability"])
    if task_msg.get("needs_local_browser") or task_msg.get("kind") == "browser_action":
        return "browser"
    if task_msg.get("needs_local_app") or task_msg.get("kind") == "app_action":
        return "app"
    return None


def root_covers(granted: str, requested: str) -> bool:
    g = os.path.normpath(os.path.expanduser(str(granted)))
    r = os.path.normpath(os.path.expanduser(str(requested)))
    return r == g or r.startswith(g + os.sep)


def pick_device_for_task(
    registry: DeviceRegistry,
    task_msg: dict[str, Any],
    *,
    now: float | None = None,
) -> Device | None:
    """Eligible device matching the task's capability, `device_id` pin, and `device_root`."""
    cap = required_capability(task_msg)
    requested_did = task_msg.get("device_id")
    root = task_msg.get("device_root")
    eligible = registry.eligible(now=now, capability=cap)

    def covers(d: Device) -> bool:
        return root is None or any(root_covers(gr, str(root)) for gr in d.roots)

    if requested_did:
        return next((d for d in eligible if d.device_id == requested_did and covers(d)), None)
    return next((d for d in eligible if covers(d)), None)


def drain_parked(
    fs: FSAdapter | None,
    registry: DeviceRegistry | None,
    audit: Any | None = None,
    *,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Move pending tasks from `device/parked` to an eligible device's mailbox `device/<device_id>`."""
    if fs is None or registry is None:
        return []

    parked_ids = list(fs.inbox_ids(PARKED_MAILBOX))
    if not parked_ids:
        return []

    drained: list[dict[str, Any]] = []
    parked_inbox = fs._agent_dir(PARKED_MAILBOX) / "inbox"

    for tid in parked_ids:
        if not fs.pending(tid):
            # Clean up stale link if task is already terminal
            if fs.done(tid):
                (parked_inbox / tid).unlink(missing_ok=True)
            continue

        try:
            msg = json.loads(fs.task_body(tid))
        except Exception:
            msg = {}

        dev = pick_device_for_task(registry, msg, now=now)
        if dev is None:
            continue

        secret = registry.secret_for(dev.device_id) if hasattr(registry, "secret_for") else None
        if secret is None:
            continue  # device never minted a secret — the agent would refuse unsigned work

        step = msg.get("step")
        if not isinstance(step, dict):
            step = {
                "kind": msg.get("kind", "echo"),
                "text": str(msg.get("prompt", "")),
                "run": msg.get("run", ""),
                "subtask": msg.get("subtask", ""),
            }
        step = {**step, "device_id": dev.device_id}
        body = {**msg, "step": step, "envelope": seal(step, secret, now=now)}

        dst = f"device/{dev.device_id}"
        dst_inbox = fs._agent_dir(dst) / "inbox"
        src_entry = parked_inbox / tid
        dst_entry = dst_inbox / tid
        task_md = fs._task_dir(tid) / "task.md"

        if not dst_entry.exists() and task_md.exists():
            meta, _ = _parse_record(task_md)
            _write_record(task_md, {**meta, "to": dst}, json.dumps(body).encode())
            os.link(task_md, dst_entry)
        src_entry.unlink(missing_ok=True)

        rec: dict[str, Any] = {
            "event": "parked_drained",
            "task": tid,
            "run": msg.get("run", ""),
            "subtask": msg.get("subtask", ""),
            "device_id": dev.device_id,
            "from_mailbox": PARKED_MAILBOX,
            "mailbox": dst,
        }
        if audit is not None and hasattr(audit, "append"):
            audit.append(rec)
        drained.append(rec)

    return drained
