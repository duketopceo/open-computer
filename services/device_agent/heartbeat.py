"""Registration and heartbeat payloads — pure functions over local facts."""

from __future__ import annotations

import platform
import time

HEARTBEAT_EVERY = 20.0  # seconds; a third of services.device.STALE_AFTER


def registration_payload(
    device_id: str, roots: list[str], capabilities: list[str] | None = None, os_name: str | None = None
) -> dict:
    return {
        "device_id": device_id,
        "os": os_name or f"{platform.system().lower()}-{platform.machine()}",
        "roots": sorted(set(roots)),
        "capabilities": sorted(set(capabilities or ["fs"])),
        "awake": True,
        "online": True,
    }


def heartbeat_payload(device_id: str, awake: bool = True, now: float | None = None) -> dict:
    return {"device_id": device_id, "awake": awake, "ts": round(time.time() if now is None else now, 3)}
