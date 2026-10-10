"""Escalation gate service — PII scan + per-step approval + append-only audit.

Conformance contract: tests/conformance/checks/__init__.py (R5 rows).
"""

from __future__ import annotations

from pathlib import Path

from .gate import CallResult, Gate
from .pii import PIIDecision, scan

__all__ = ["CallResult", "Gate", "PIIDecision", "scan", "adapter"]


def adapter(root: Path | str, **_: object) -> Gate:
    return Gate(Path(root) / "audit.ndjson")
