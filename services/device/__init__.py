"""Device plane — registry of user machines that can run `device` steps (plan P1).

Conformance: tests/unit/test_device_registry.py. Grants and the file
capability land in P2; this unit only knows who is here and how to sign
steps to them.
"""

from __future__ import annotations

from pathlib import Path

from .protocol import WINDOW, EnvelopeError, mint_secret, open_envelope, seal
from .registry import CAPABILITIES, STALE_AFTER, Device, DeviceRegistry

__all__ = [
    "CAPABILITIES",
    "STALE_AFTER",
    "WINDOW",
    "Device",
    "DeviceRegistry",
    "EnvelopeError",
    "mint_secret",
    "open_envelope",
    "seal",
    "adapter",
]


def adapter(root: Path | str, **kw: object) -> DeviceRegistry:
    return DeviceRegistry(Path(root) / "devices", **kw)  # type: ignore[arg-type]
