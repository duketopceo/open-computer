"""Router — engine chain + budget gate + declared deterministic fallback.

Chain: decider -> jev -> table. Never openrouter/auto (bench: worst
accuracy, 17x cost). A 'table' decision is honest about what it is.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

from . import decide as _decide
from .budget import Budget, BudgetHalt  # noqa: F401  (re-exported)
from .policy_table import offline_decide


@dataclass
class RouteDecision:
    route: str
    tier: str
    engine: str
    confidence: float | None
    cost: float
    ms: float
    model_used: str | None = None


Engine = Callable[[str], RouteDecision]


def _http_engine(name: str, model: str, base_url: str, api_key: str) -> Engine:
    def run(utterance: str) -> RouteDecision:
        d = _decide.ask_decisions(
            f'User request: "{utterance}"',
            base_url=base_url,
            api_key=api_key,
            model=model,
        )
        return RouteDecision(
            route=d["route"],
            tier=d["tier"],
            engine=name,
            confidence=d["confidence"],
            cost=d["cost"],
            ms=d["ms"],
            model_used=d["model_used"],
        )

    return run


def _table_engine() -> Engine:
    def run(utterance: str) -> RouteDecision:
        d = offline_decide(utterance)
        return RouteDecision(
            route=d["route"],
            tier=d["tier"],
            engine="table",
            confidence=d["confidence"],
            cost=0.0,
            ms=0.0,
        )

    return run


class Router:
    def __init__(self, budget: Budget, engines: list[Engine]):
        self.budget = budget
        self.engines = engines

    def route(self, utterance: str) -> RouteDecision:
        self.budget.check()
        for eng in self.engines:
            try:
                d = eng(utterance)
            except Exception:
                continue  # engine down/invalid — next in chain
            if not (d.route and d.tier):
                continue  # malformed decision — reject, next engine
            self.budget.record(d.cost, d.engine, d.route)
            return d
        # every engine down or invalid -> deterministic table, declared
        d = _table_engine()(utterance)
        self.budget.record(0.0, "table", d.route)
        return d


def default_router(
    ledger_path,
    base_url: str | None = None,
    api_key: str | None = None,
    daily_cap: float = 1.0,
    task_cap: float = 0.10,
) -> Router:
    """decider -> jev -> (implicit table). Config via env or args."""
    base = (base_url or os.environ.get("OPENROUTER_BASE") or _decide.DEFAULT_BASE).rstrip("/")
    key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
    engines: list[Engine] = []
    if key:
        engines.append(_http_engine("decider", _decide.DECIDER_MODEL, base, key))
        engines.append(_http_engine("jev", _decide.JEV_MODEL, base, key))
    return Router(Budget(ledger_path, daily_cap=daily_cap, task_cap=task_cap), engines)
