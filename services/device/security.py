"""Headless pairing (RFC 6238 TOTP), token refresh, and emergency kill switch (plan P6).

- `mint_totp_secret`, `generate_totp`, `verify_totp`: pure-stdlib RFC 6238 / RFC 4226
  TOTP implementation (HMAC-SHA1, 30 s step, 6 digits).
- `pair_device` and `refresh_device_token`: require a valid TOTP code before
  registering a headless device or rotating an expired device token.
- `kill_device`: emergency kill switch that revokes all grants for `device_id`,
  deletes its `.secret` (and `.token.json`) file, marks the device offline in
  the registry, fails any queued tasks in `device/<device_id>`, and writes a
  `device_killed` audit event.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets as _secrets
import struct
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from oc.fsproto import adapter as fs_adapter
from services.gate.audit import AuditLog

from .grants import GrantStore
from .protocol import mint_secret
from .registry import Device, DeviceRegistry

TOTP_STEP = 30
TOTP_DIGITS = 6
DEFAULT_TOKEN_TTL = 3600.0


class PairingError(PermissionError):
    """Raised when headless pairing or token refresh fails TOTP verification."""


@dataclass
class DeviceToken:
    device_id: str
    secret: str
    issued_at: float
    expires_at: float
    revoked: bool = False

    def active(self, now: float) -> bool:
        return not self.revoked and now <= self.expires_at


DEVICE_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,128}")
# Mailbox namespace is `device/<id>` — "." / ".." escape it; "parked" is the global
# parked queue and must never be claimable as a device.
RESERVED_DEVICE_IDS = frozenset({".", "..", "parked"})


def valid_device_id(did: str) -> bool:
    return bool(DEVICE_ID_RE.fullmatch(did)) and did not in RESERVED_DEVICE_IDS


def mint_totp_secret(nbytes: int = 20) -> str:
    """Mint a random RFC 3548 Base32 secret (without padding) for TOTP."""
    raw = _secrets.token_bytes(nbytes)
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def _decode_totp_secret(secret: str) -> bytes:
    cleaned = "".join(secret.strip().split()).upper()
    pad_len = (-len(cleaned)) % 8
    padded = cleaned + ("=" * pad_len)
    try:
        return base64.b32decode(padded, casefold=True)
    except Exception:
        return secret.encode("utf-8")


def generate_totp(
    secret: str,
    *,
    now: float | None = None,
    step: int = TOTP_STEP,
    digits: int = TOTP_DIGITS,
) -> str:
    """Compute an RFC 6238 TOTP code (HMAC-SHA1) at `now`."""
    t = time.time() if now is None else now
    counter = int(t // step)
    msg = struct.pack(">Q", counter)
    key = _decode_totp_secret(secret)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    code = binary % (10**digits)
    return f"{code:0{digits}d}"


def verify_totp(
    secret: str,
    code: str | None,
    *,
    now: float | None = None,
    step: int = TOTP_STEP,
    digits: int = TOTP_DIGITS,
    skew_steps: int = 1,
) -> bool:
    """Verify an RFC 6238 TOTP code within `±skew_steps` time steps."""
    if not code or not isinstance(code, str):
        return False
    candidate = code.strip()
    if len(candidate) != digits or not candidate.isdigit():
        return False
    t = time.time() if now is None else now
    for delta in range(-skew_steps, skew_steps + 1):
        expected = generate_totp(secret, now=t + (delta * step), step=step, digits=digits)
        if hmac.compare_digest(expected, candidate):
            return True
    return False


def _devices_dir(root: Path | str) -> Path:
    p = Path(root)
    return p if p.name == "devices" else p / "devices"


def _write_device_token(dev_dir: Path, tok: DeviceToken) -> None:
    dev_dir.mkdir(parents=True, exist_ok=True)
    secret_file = dev_dir / f"{tok.device_id}.secret"
    secret_file.write_text(tok.secret, encoding="utf-8")
    try:
        secret_file.chmod(0o600)
    except OSError:
        pass
    meta_file = dev_dir / f"{tok.device_id}.token.json"
    tmp = meta_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(tok), indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, meta_file)


def pair_device(
    root: Path | str,
    payload: dict[str, Any],
    *,
    totp_secret: str,
    totp_code: str | None,
    registry: DeviceRegistry | None = None,
    now: float | None = None,
    token_ttl: float = DEFAULT_TOKEN_TTL,
    audit: Any | None = None,
) -> tuple[Device, DeviceToken]:
    """Pair a headless device after verifying `totp_code`."""
    t = time.time() if now is None else now
    did = str(payload.get("device_id") or "")
    if not valid_device_id(did):
        raise PairingError(f"invalid device_id: {did!r}")
    if not verify_totp(totp_secret, totp_code, now=t):
        if audit is not None and hasattr(audit, "append"):
            audit.append({"event": "device_pair_refused", "device_id": did, "reason": "invalid_totp"})
        raise PairingError("valid TOTP code required for headless pairing")

    dev_dir = _devices_dir(root)
    reg = registry if registry is not None else DeviceRegistry(dev_dir, clock=lambda: t)
    dev = reg.register(payload)
    tok = DeviceToken(
        device_id=did,
        secret=mint_secret(),
        issued_at=t,
        expires_at=t + float(token_ttl),
    )
    _write_device_token(dev_dir, tok)
    if audit is not None and hasattr(audit, "append"):
        audit.append({"event": "device_paired", "device_id": did, "expires_at": tok.expires_at})
    return dev, tok


def refresh_device_token(
    root: Path | str,
    device_id: str,
    *,
    totp_secret: str,
    totp_code: str | None,
    now: float | None = None,
    token_ttl: float = DEFAULT_TOKEN_TTL,
    audit: Any | None = None,
) -> DeviceToken:
    """Rotate an expired or expiring device token after verifying `totp_code`."""
    t = time.time() if now is None else now
    if not verify_totp(totp_secret, totp_code, now=t):
        if audit is not None and hasattr(audit, "append"):
            audit.append(
                {
                    "event": "device_token_refresh_refused",
                    "device_id": device_id,
                    "reason": "invalid_totp",
                }
            )
        raise PairingError("valid TOTP code required for token refresh")

    dev_dir = _devices_dir(root)
    tok = DeviceToken(
        device_id=device_id,
        secret=mint_secret(),
        issued_at=t,
        expires_at=t + float(token_ttl),
    )
    _write_device_token(dev_dir, tok)
    if audit is not None and hasattr(audit, "append"):
        audit.append({"event": "device_token_refreshed", "device_id": device_id, "expires_at": tok.expires_at})
    return tok


def kill_device(
    root: Path | str,
    device_id: str,
    registry: DeviceRegistry | None = None,
    grants: GrantStore | None = None,
    fs: Any | None = None,
    audit: Any | None = None,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """Emergency kill switch for `device_id`.

    1. Revokes all active grants for `device_id`.
    2. Deletes `<device_id>.secret` (and `<device_id>.token.json`).
    3. Marks `device_id` offline (and not awake) in `DeviceRegistry`.
    4. Fails any queued or leased tasks in mailbox `device/<device_id>`.
    5. Appends a `device_killed` event to `audit`.
    """
    base = Path(root)
    dev_dir = _devices_dir(base)
    t = time.time() if now is None else now

    reg = registry if registry is not None else DeviceRegistry(dev_dir, clock=lambda: t)
    store = grants if grants is not None else GrantStore(dev_dir, clock=lambda: t)
    fs_inst = fs if fs is not None else fs_adapter(base / "run", clock=lambda: t)
    audit_inst = audit if audit is not None else AuditLog(base / "orchestrator.ndjson")

    revoked_grants: list[str] = []
    for g in store.list(device_id, include_inactive=True, now=t):
        if not g.revoked:
            store.revoke(g.grant_id, now=t)
            revoked_grants.append(g.grant_id)

    secret_deleted = False
    for path in (
        dev_dir / f"{device_id}.secret",
        base / f"{device_id}.secret",
        dev_dir / f"{device_id}.token.json",
        base / f"{device_id}.token.json",
    ):
        if path.exists():
            path.unlink(missing_ok=True)
            if path.suffix == ".secret":
                secret_deleted = True

    dev = reg.get(device_id)
    if dev is not None:
        dev.online = False
        dev.awake = False
        reg._save()

    mailbox = f"device/{device_id}"
    failed_tasks: list[str] = []
    for tid in list(fs_inst.inbox_ids(mailbox)):
        st_path = fs_inst._task_dir(tid) / "status"
        st = st_path.read_text().strip() if st_path.exists() else "queued"
        if st not in ("done", "failed") and not fs_inst.done(tid):
            fs_inst.fail(tid)
            failed_tasks.append(tid)
        (fs_inst._agent_dir(mailbox) / "inbox" / tid).unlink(missing_ok=True)

    rec: dict[str, Any] = {
        "event": "device_killed",
        "device_id": device_id,
        "revoked_grants": revoked_grants,
        "secret_deleted": secret_deleted,
        "failed_tasks": failed_tasks,
        "online": False,
    }
    if audit_inst is not None and hasattr(audit_inst, "append"):
        audit_inst.append(rec)
    return rec
