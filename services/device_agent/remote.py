"""Remote-mode client for the device agent (plan P8).

Mirrors, method for method, the call sites in `device_agent.main`'s drain loop —
`fs.inbox_ids`, `fs.claim`, `fs.task_body`, `fs.outbox_write`, `reg.heartbeat`,
`reg.get`, `audit.append` — over the authenticated relay API. The same device
secret that opens sealed envelopes is the bearer credential; the relay holds the
durable fsproto spine.

Errors surface as `RemoteError` (transport) or the *same* fsproto exceptions the
local path raises (`AlreadyLeased`, `TaskFinal`, `ResultImmutable`) — the drain
loop doesn't need to know which transport it is on.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from oc.fsproto import AlreadyLeased, ResultImmutable, TaskFinal
from services.device.grants import GrantStore


class RemoteError(RuntimeError):
    """Transport failure or an unexpected relay response."""


class RemoteClient:
    def __init__(self, base_url: str, device_id: str, secret: str, *, timeout: float = 10.0):
        self.base = base_url.rstrip("/")
        self.device_id = device_id
        self._secret = secret
        self._timeout = timeout
        self._device: dict | None = None

    # --- transport -----------------------------------------------------------
    def _req(self, method: str, path: str, body: bytes | None = None) -> tuple[int, bytes]:
        url = f"{self.base}/relay/devices/{self.device_id}{path}"
        req = urllib.request.Request(
            url,
            method=method,
            data=body,
            headers={
                "Authorization": f"Bearer {self._secret}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise RemoteError(f"relay {method} {path}: {e}") from e

    def _json(self, method: str, path: str, body: dict | None = None) -> dict:
        status, raw = self._req(method, path, json.dumps(body).encode() if body is not None else None)
        if status == 401:
            raise RemoteError("relay rejected the device credential (expired, revoked or wrong)")
        if status != 200:
            raise RemoteError(f"relay {method} {path}: HTTP {status} {raw[:120]!r}")
        try:
            return json.loads(raw)
        except ValueError as e:
            raise RemoteError(f"relay {method} {path}: bad json response") from e

    # --- registry duck -------------------------------------------------------
    def heartbeat(self, device_id: str | None = None, **_kw) -> Any:
        """`reg.heartbeat(did)` — also carries the parked-drain trigger server-side."""
        did = device_id or self.device_id
        status, raw = self._req("POST", "/heartbeat")
        if status == 404:
            raise KeyError(did)
        if status == 401:
            raise RemoteError("heartbeat refused: credential expired or revoked")
        if status != 200:
            raise RemoteError(f"heartbeat: HTTP {status}")
        data = json.loads(raw)
        self._device = data.get("device")
        return SimpleNamespace(**(self._device or {}))

    def get(self, device_id: str) -> Any | None:
        """`reg.get(did)` — cached from the last heartbeat; None before the first."""
        if self._device is None or self._device.get("device_id") != device_id:
            return None
        return SimpleNamespace(**self._device)

    # --- fs duck -------------------------------------------------------------
    def inbox_ids(self, mailbox: str) -> list[str]:
        """`fs.inbox_ids(mailbox)` — mailbox is always this device's own."""
        return list(self._json("GET", "/inbox").get("tasks", []))

    def claim(self, mailbox: str, tid: str, ttl: float | None = None) -> str:
        """`fs.claim(mailbox, tid, ttl)` — raises AlreadyLeased / TaskFinal like the local path."""
        status, _ = self._req("POST", f"/tasks/{tid}/claim", json.dumps({"ttl": ttl}).encode())
        if status == 200:
            return tid
        if status == 409:
            raise AlreadyLeased(tid)
        if status == 410:
            raise TaskFinal(tid)
        raise RemoteError(f"claim {tid}: HTTP {status}")

    def task_body(self, tid: str) -> bytes:
        """`fs.task_body(tid)` — raw task record body (JSON containing the sealed envelope)."""
        data = self._json("GET", f"/tasks/{tid}")
        return json.dumps(data["body"]).encode()

    def outbox_write(self, mailbox: str, tid: str, payload: bytes) -> None:
        """`fs.outbox_write(mailbox, tid, body)` — tombstoned results are immutable."""
        status, _ = self._req("POST", f"/tasks/{tid}/result", payload)
        if status == 200:
            return
        if status == 409:
            raise ResultImmutable(tid)
        raise RemoteError(f"result {tid}: HTTP {status}")

    # --- audit duck ----------------------------------------------------------
    def append(self, event: dict) -> None:
        """`audit.append(event)` — relay stamps device_id + via=relay server-side."""
        try:
            self._json("POST", "/audit", event)
        except RemoteError:
            pass  # audit loss must never block execution; the result write is the durable record

    # --- grants --------------------------------------------------------------
    def grant_store(self, state_dir: Path) -> GrantStore:
        """Sync the device's grant file and return a GrantStore over it (full local fidelity)."""
        data = self._json("GET", "/grants").get("grants", {})
        dev_dir = Path(state_dir) / "devices"
        dev_dir.mkdir(parents=True, exist_ok=True)
        tmp = dev_dir / "grants.json.tmp"
        tmp.write_text(json.dumps(data))
        tmp.replace(dev_dir / "grants.json")
        return GrantStore(state_dir)
