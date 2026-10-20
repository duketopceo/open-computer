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
import math
import re
import time
from pathlib import Path
from typing import Any

from services.device.registry import STALE_AFTER

PARKED = "device/parked"

# Registry keys become mailbox path segments — only ids the agent could legitimately use.
DEVICE_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
RESERVED = {"parked", ".", ".."}


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
        if not sf.exists():
            return "queued"
        with sf.open("rb") as f:
            return f.read(64).decode(errors="replace").strip()
    except OSError:
        return "unknown"


def _mailbox_depth(run_root: Path, mailbox: str) -> int:
    """Count non-terminal tasks offered in `mailbox` (queued or leased)."""
    inbox = run_root / "agents" / mailbox / "inbox"
    try:
        if not inbox.is_dir():
            return 0
        entries = list(inbox.iterdir())
    except OSError:
        return 0
    return sum(1 for p in entries if _task_status(run_root, p.name) in ("queued", "leased"))


def _num(v: Any, default: float = 0.0) -> float:
    """Finite float or the default — malformed/non-finite values degrade, never crash."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def _str_list(v: Any) -> list[str]:
    return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []


def collect_devices(root: Path | str, *, now: float | None = None, stale_after: float = STALE_AFTER) -> dict[str, Any]:
    """Return `{"devices": [...], "parked_depth", "online", "eligible", "total"}`."""
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
        if g.get("revoked") or t > _num(g.get("expires_at")):
            continue
        did = str(g.get("device_id", ""))
        active_grants[did] = active_grants.get(did, 0) + 1

    rows: list[dict[str, Any]] = []
    for did in sorted(registry):
        d = registry[did]
        if not isinstance(d, dict) or not DEVICE_ID.fullmatch(did) or did in RESERVED:
            continue
        last = _num(d.get("last_heartbeat"))
        if last < 0 or last > t + 60:  # beyond small clock skew = corrupt; page can't render 1e20 anyway
            last = 0.0
        online = bool(d.get("online", False)) and (t - last) <= stale_after
        awake = bool(d.get("awake", False))
        rows.append(
            {
                "device_id": did,
                "os": d["os"] if isinstance(d.get("os"), str) else "unknown",
                "capabilities": _str_list(d.get("capabilities")),
                "roots": _str_list(d.get("roots")),
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
        "online": sum(1 for r in rows if r["online"]),
        "eligible": sum(1 for r in rows if r["eligible"]),
        "parked_depth": _mailbox_depth(run_root, PARKED),
    }
