"""Escalation gate service — PII scan + per-step approval + append-only audit.

Conformance contract: tests/conformance/checks/__init__.py (R5 rows).
"""

from __future__ import annotations

from pathlib import Path

from .gate import Approval, CallResult, Gate, payload_hash
from .pii import PIIDecision, scan

__all__ = ["Approval", "CallResult", "Gate", "PIIDecision", "payload_hash", "scan", "adapter"]


def adapter(root: Path | str, **kw: object) -> Gate:
    return Gate(Path(root) / "audit.ndjson", **kw)  # type: ignore[arg-type]
