"""Device plane — registry, grants, parked-mailbox drain, and security (plan P1–P6).

Conformance: tests/unit/test_device_registry.py, tests/unit/test_grants.py,
tests/unit/test_device_p4_p7.py.
"""

from __future__ import annotations

from pathlib import Path

from .grants import DEFAULT_GRANT_TTL, MODES, Grant, GrantCheckResult, GrantStore
from .parked import PARKED_MAILBOX, drain_parked
from .protocol import WINDOW, EnvelopeError, mint_secret, open_envelope, seal
from .registry import CAPABILITIES, STALE_AFTER, Device, DeviceRegistry
from .security import (
    DEFAULT_TOKEN_TTL,
    TOTP_DIGITS,
    TOTP_STEP,
    DeviceToken,
    PairingError,
    generate_totp,
    kill_device,
    mint_totp_secret,
    pair_device,
    refresh_device_token,
    verify_totp,
)

__all__ = [
    "CAPABILITIES",
    "DEFAULT_GRANT_TTL",
    "DEFAULT_TOKEN_TTL",
    "MODES",
    "PARKED_MAILBOX",
    "STALE_AFTER",
    "TOTP_DIGITS",
    "TOTP_STEP",
    "WINDOW",
    "Device",
    "DeviceRegistry",
    "DeviceToken",
    "EnvelopeError",
    "Grant",
    "GrantCheckResult",
    "GrantStore",
    "PairingError",
    "adapter",
    "drain_parked",
    "generate_totp",
    "kill_device",
    "mint_secret",
    "mint_totp_secret",
    "open_envelope",
    "pair_device",
    "refresh_device_token",
    "seal",
    "verify_totp",
]


def adapter(root: Path | str, **kw: object) -> DeviceRegistry:
    return DeviceRegistry(Path(root) / "devices", **kw)  # type: ignore[arg-type]
