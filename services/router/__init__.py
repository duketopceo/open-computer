"""Router service — per-subtask model selection (U4).

decider primary -> jev fallback -> deterministic table. Budget-gated,
never silent-auto. Conformance: tests/conformance/checks/router_checks.py.
"""

from __future__ import annotations

from pathlib import Path

from .budget import Budget, BudgetHalt
from .router import RouteDecision, Router, default_router

__all__ = ["Budget", "BudgetHalt", "RouteDecision", "Router", "adapter", "default_router"]


def adapter(root: Path | str, **kw: object) -> Router:
    return default_router(Path(root) / "router-ledger.jsonl", **kw)
