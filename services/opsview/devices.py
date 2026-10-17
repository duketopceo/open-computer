"""Device column for the operator console (plan P5).

`collect_devices(root)` reads the device plane's on-disk state and reports,
per registered device: online/awake/eligible state, active grants count, and
inbox depth, plus the depth of the shared `device/parked` mailbox.

Read-only: never creates files, never marks a device offline (the registry
does that on its own read path). Layout under `root`:

    devices/devices.json   registry (P1)
    devices/grants.json    grant store (P2)
    run/agents/device/<id>/inbox, run/agents/device/parked/inbox   fsproto mailboxes
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from services.device.registry import STALE_AFTER

PARKED = "device/parked"


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _task_status(run_root: Path, tid: str) -> str:
    sf = run_root / "tasks" / tid / "status"
    try:
        return sf.read_text().strip() if sf.exists() else "queued"
    except OSError:
        return "unknown"


def _mailbox_depth(run_root: Path, mailbox: str) -> int:
    """Count non-terminal tasks offered in `mailbox` (queued or leased)."""
    inbox = run_root / "agents" / mailbox / "inbox"
    if not inbox.is_dir():
        return 0
    return sum(1 for p in inbox.iterdir() if _task_status(run_root, p.name) in ("queued", "leased"))


def collect_devices(root: Path | str, *, now: float | None = None, stale_after: float = STALE_AFTER) -> dict[str, Any]:
    """Return `{"devices": [...], "parked_depth": int, "online": int, "total": int}`."""
    base = Path(root)
    dev_dir = base / "devices"
    run_root = base / "run"
    t = time.time() if now is None else now

    registry = _load_json(dev_dir / "devices.json")
    grants = _load_json(dev_dir / "grants.json")

    active_grants: dict[str, int] = {}
    for g in grants.values():
        if not isinstance(g, dict):
            continue
        if g.get("revoked") or t > float(g.get("expires_at", 0)):
            continue
        did = str(g.get("device_id", ""))
        active_grants[did] = active_grants.get(did, 0) + 1

    rows: list[dict[str, Any]] = []
    for did in sorted(registry):
        d = registry[did]
        if not isinstance(d, dict):
            continue
        last = float(d.get("last_heartbeat", 0.0))
        online = bool(d.get("online", False)) and (t - last) <= stale_after
        awake = bool(d.get("awake", False))
        rows.append(
            {
                "device_id": did,
                "os": d.get("os", "unknown"),
                "capabilities": list(d.get("capabilities", [])),
                "roots": list(d.get("roots", [])),
                "online": online,
                "awake": awake,
                "eligible": online and awake,
                "state": "online" if online and awake else ("asleep" if online else "offline"),
                "last_heartbeat": last,
                "heartbeat_age_s": round(max(0.0, t - last), 1) if last else None,
                "active_grants": active_grants.get(did, 0),
                "inbox_depth": _mailbox_depth(run_root, f"device/{did}"),
            }
        )

    return {
        "devices": rows,
        "total": len(rows),
        "online": sum(1 for r in rows if r["eligible"]),
        "parked_depth": _mailbox_depth(run_root, PARKED),
    }
