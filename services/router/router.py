"""Router — engine chain + budget gate + declared deterministic fallback.

Chain: decider -> jev -> table. Never openrouter/auto (bench: worst
accuracy, 17x cost). A 'table' decision is honest about what it is.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Callable

from oc.obs import capture_content, get_logger

from . import decide as _decide
from .budget import Budget, BudgetHalt  # noqa: F401  (re-exported)
from .policy_table import offline_decide

log = get_logger("router")


@dataclass
class RouteDecision:
    route: str
    tier: str
    engine: str
    confidence: float | None
    cost: float
    ms: float
    model_used: str | None = None
    router_model: str | None = None  # the model id the engine asked (model_used is the one that answered)
    fallbacks: list[dict] = field(default_factory=list)  # engines tried first: {engine, kind, status, reason}


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
            router_model=model,
        )

    run.__name__ = name
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
        self._skipped: dict[str, dict] = {}  # engine name -> the fallback entry that retired it (404 no endpoints)

    def route(self, utterance: str) -> RouteDecision:
        self.budget.check()
        t0 = time.monotonic()
        fallbacks: list[dict] = []
        for eng in self.engines:
            name = getattr(eng, "__name__", "engine")
            if name in self._skipped:
                fallbacks.append(self._skipped[name])  # retired for this process; every step still shows why
                continue
            try:
                d = eng(utterance)
            except Exception as e:
                fb = {"engine": name, "kind": type(e).__name__, "status": getattr(e, "status", None),
                      "reason": getattr(e, "reason", "")}
                fallbacks.append(fb)
                log.warning("engine down, trying next", engine=name, error=fb["kind"], status=fb["status"],
                            **({"reason": fb["reason"]} if capture_content() else {}))
                if fb["status"] == 404 and "No endpoints" in fb["reason"]:
                    self._skipped[name] = fb  # the model is gone: do not ask again until the process restarts
                    log.warning("engine retired: no endpoints for its model", engine=name)
                continue  # engine down/invalid — next in chain
            if not (d.route and d.tier):
                log.warning("malformed decision rejected", engine=d.engine)
                fallbacks.append({"engine": name, "kind": "MalformedDecision", "status": None, "reason": ""})
                continue  # malformed decision — reject, next engine
            d.fallbacks = fallbacks
            self.budget.record(d.cost, d.engine, d.route)
            self._log_decision(d, utterance, t0)
            return d
        # every engine down or invalid -> deterministic table, declared
        d = _table_engine()(utterance)
        d.fallbacks = fallbacks
        self.budget.record(0.0, "table", d.route)
        log.warning("all engines down: table fallback", engines=len(self.engines))
        self._log_decision(d, utterance, t0)
        return d

    @staticmethod
    def _log_decision(d: "RouteDecision", utterance: str, t0: float) -> None:
        log.info("route decided", route=d.route, tier=d.tier, engine=d.engine, confidence=d.confidence,
                 cost=d.cost, ms=round((time.monotonic() - t0) * 1000, 1), model=d.model_used)
        if log.enabled_debug():
            log.debug("route input", utterance_chars=len(utterance))


def default_router(
    ledger_path,
    base_url: str | None = None,
    api_key: str | None = None,
    daily_cap: float | None = None,
    task_cap: float | None = None,
) -> Router:
    """decider -> jev -> (implicit table). Config via env or args."""
    base = (base_url or os.environ.get("OPENROUTER_BASE") or _decide.DEFAULT_BASE).rstrip("/")
    key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
    engines: list[Engine] = []
    if key:
        decider = os.environ.get("OC_DECIDER_MODEL") or _decide.DECIDER_MODEL
        engines.append(_http_engine("decider", decider, base, key))
        engines.append(_http_engine("jev", _decide.JEV_MODEL, base, key))
    daily = daily_cap if daily_cap is not None else float(os.environ.get("OC_DAILY_CAP", "1.0"))
    task = task_cap if task_cap is not None else float(os.environ.get("OC_TASK_CAP", "0.10"))
    return Router(Budget(ledger_path, daily_cap=daily, task_cap=task), engines)
