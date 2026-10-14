"""Placement service — subtask -> local/cloud target (U5).

Deterministic rules only, conformance: tests/conformance/checks/
placement_checks.py (PLACE rows).
"""

from __future__ import annotations

from pathlib import Path

from .rules import TARGETS, Placement, place
from .health import local_endpoints_up, place_live

__all__ = ["TARGETS", "Placement", "place", "place_live", "local_endpoints_up", "adapter"]


def adapter(root: Path | str, **_: object):
    """Placement is stateless — the adapter keeps the fixture contract."""
    return place
