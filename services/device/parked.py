"""Parked device mailbox drain — moves queued tasks from `device/parked` to `device/<id>` (plan P5).

When a step requiring a user device is dispatched while no device is eligible,
`services/orchestrator/dispatch.py` places the task in mailbox `device/parked`.
When an eligible device registers or heartbeats, `drain_parked(fs, registry, audit)`
moves pending tasks from `device/parked` into `device/<device_id>` and appends a
`parked_drained` audit event for each moved task.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from oc.fsproto import FSAdapter
    from services.device.registry import Device, DeviceRegistry

PARKED_MAILBOX = "device/parked"


def _required_capability(task_msg: dict[str, Any]) -> str | None:
    if task_msg.get("capability"):
        return str(task_msg["capability"])
    if task_msg.get("needs_local_browser") or task_msg.get("kind") == "browser_action":
        return "browser"
    if task_msg.get("needs_local_app") or task_msg.get("kind") == "app_action":
        return "app"
    return None


def _pick_device_for_task(
    registry: DeviceRegistry,
    task_msg: dict[str, Any],
    *,
    now: float | None = None,
) -> Device | None:
    cap = _required_capability(task_msg)
    requested_did = task_msg.get("device_id")
    if requested_did:
        eligible = registry.eligible(now=now, capability=cap)
        return next((d for d in eligible if d.device_id == requested_did), None)
    return registry.pick(capability=cap, now=now)


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

        dev = _pick_device_for_task(registry, msg, now=now)
        if dev is None:
            continue

        dst = f"device/{dev.device_id}"
        dst_inbox = fs._agent_dir(dst) / "inbox"
        src_entry = parked_inbox / tid
        dst_entry = dst_inbox / tid
        task_md = fs._task_dir(tid) / "task.md"

        if not dst_entry.exists() and task_md.exists():
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
