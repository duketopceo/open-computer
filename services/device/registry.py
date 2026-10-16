"""Device registry — which user machines can take `device` steps (plan P1).

A device is a user-owned computer running `services/device_agent`. It
registers once (idempotent), heartbeats on a fixed cadence, and is only
eligible for placement while its last heartbeat is fresh. Everything is
a JSON file under `root` so the opsview and tests read the same words.

Provenance: documentation-based. Mirrors the publicly described
Personal Computer contract — the device must be awake, online and
registered — without any claim about the vendor's implementation.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

STALE_AFTER = 60.0  # seconds without a heartbeat before a device drops out of placement
CAPABILITIES = ("fs", "app", "browser", "shell")


@dataclass
class Device:
    device_id: str
    os: str
    roots: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    awake: bool = True
    online: bool = True
    registered_at: float = 0.0
    last_heartbeat: float = 0.0

    def fresh(self, now: float, stale_after: float = STALE_AFTER) -> bool:
        return self.online and self.awake and (now - self.last_heartbeat) <= stale_after


class DeviceRegistry:
    def __init__(self, root: Path | str, clock: Callable[[], float] = time.time):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._path = self.root / "devices.json"
        self._clock = clock
        self._devices: dict[str, Device] = {}
        if self._path.exists():
            for d in json.loads(self._path.read_text()).values():
                self._devices[d["device_id"]] = Device(**d)

    # ── persistence ────────────────────────────────────────────────
    def _save(self) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps({k: asdict(v) for k, v in self._devices.items()}, indent=1, sort_keys=True))
        os.replace(tmp, self._path)

    # ── protocol ───────────────────────────────────────────────────
    def register(self, payload: dict) -> Device:
        """Idempotent: re-registering a known device updates roots/capabilities and refreshes its heartbeat."""
        did = payload["device_id"]
        caps = [c for c in payload.get("capabilities", []) if c in CAPABILITIES]
        now = self._clock()
        dev = self._devices.get(did)
        if dev is None:
            dev = Device(device_id=did, os=payload.get("os", "unknown"), registered_at=now)
            self._devices[did] = dev
        dev.os = payload.get("os", dev.os)
        dev.roots = sorted(set(payload.get("roots", [])))
        dev.capabilities = sorted(set(caps))
        dev.awake = bool(payload.get("awake", True))
        dev.online = bool(payload.get("online", True))
        dev.last_heartbeat = now
        self._save()
        return dev

    def heartbeat(self, device_id: str, *, awake: bool = True, now: float | None = None) -> Device:
        dev = self._devices[device_id]  # KeyError: unregistered devices must register first
        dev.last_heartbeat = self._clock() if now is None else now
        dev.awake = awake
        dev.online = True
        self._save()
        return dev

    def get(self, device_id: str) -> Device | None:
        return self._devices.get(device_id)

    def eligible(
        self, *, now: float | None = None, stale_after: float = STALE_AFTER, capability: str | None = None
    ) -> list[Device]:
        """Devices a `device` step may be sent to right now. Stale ones are excluded and marked offline."""
        t = self._clock() if now is None else now
        out = []
        changed = False
        for dev in self._devices.values():
            if not dev.fresh(t, stale_after):
                if dev.online:
                    dev.online = False
                    changed = True
                continue
            if capability and capability not in dev.capabilities:
                continue
            out.append(dev)
        if changed:
            self._save()
        return sorted(out, key=lambda d: d.device_id)

    def pick(self, *, capability: str | None = None, now: float | None = None) -> Device | None:
        """First eligible device, or None when the run must park until a heartbeat arrives."""
        devs = self.eligible(now=now, capability=capability)
        return devs[0] if devs else None
