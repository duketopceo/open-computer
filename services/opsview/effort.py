"""Effort levels: one pick that chooses the model and the per-run budget.

The composer shows Low / Medium / High instead of a list of settings. Each level names a router tier
(`services.router.policy_table.TIERS`); the model is that tier's first candidate in `TIER_MODELS`, so the
spend rule (ROADMAP.md, enforced by tests/unit/test_spend_rule.py) governs it. The per-run budget is a
hard cap on a `CostLedger` that pauses the run, and is never above the daily cap (`OC_DAILY_CAP`).

The optional advanced override (a model from the three below, a smaller or larger budget) goes through
`resolve`, which refuses anything outside that set and anything above the daily cap.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from services.router.policy_table import TIER_MODELS


@dataclass(frozen=True)
class Level:
    id: str
    label: str
    blurb: str
    tier: str
    budget: float  # dollars per run, before the daily-cap clamp

    @property
    def model(self) -> str:
        return TIER_MODELS[self.tier][0]


LEVELS: tuple[Level, ...] = (
    Level("low", "Low", "Quick and cheap: a hosted flash-class model.", "cheap", 0.10),
    Level("medium", "Medium", "Harder tasks: a strong mid-tier model.", "mid", 0.30),
    Level("high", "High", "Most capable allowed: the price ceiling, never a default.", "frontier", 1.00),
)
BY_ID = {lv.id: lv for lv in LEVELS}
FALLBACK_DEFAULT = "low"


@dataclass(frozen=True)
class Plan:
    """What one submitted goal will run with. `budget` is dollars for this run."""

    effort: str
    model: str
    budget: float


def default_id() -> str:
    """`OC_EFFORT_DEFAULT` if it names a level, otherwise Low. High is never the fallback."""
    v = os.environ.get("OC_EFFORT_DEFAULT", "").strip().lower()
    return v if v in BY_ID else FALLBACK_DEFAULT


def models() -> list[str]:
    """Models the advanced override may pick: exactly the ones the levels use."""
    return list(dict.fromkeys(lv.model for lv in LEVELS))


def _budget(raw: object) -> float | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        raise ValueError("budget must be a number of dollars")
    try:
        v = float(raw)
    except ValueError:
        raise ValueError("budget must be a number of dollars") from None
    if not v > 0 or v == float("inf"):
        raise ValueError("budget must be a positive number of dollars")
    return v


def resolve(effort: object, model: object, budget: object, daily_cap: float) -> Plan:
    """Turn a request into a Plan. Raises ValueError (a 4xx for the caller) for unknown or over-cap input."""
    eid = default_id() if effort in (None, "") else effort
    lv = BY_ID.get(eid) if isinstance(eid, str) else None
    if lv is None:
        raise ValueError(f"unknown effort {effort!r}; choose one of {', '.join(BY_ID)}")
    picked = lv.model
    if model not in (None, ""):
        if model not in models():
            raise ValueError(f"model {model!r} is not allowed; choose one of {', '.join(models())}")
        picked = str(model)
    asked = _budget(budget)
    if asked is not None and asked > daily_cap:
        raise ValueError(f"budget {asked:g} is above the daily cap {daily_cap:g}")
    return Plan(lv.id, picked, round(asked if asked is not None else min(lv.budget, daily_cap), 6))


def catalog(daily_cap: float) -> dict:
    """The payload behind GET /api/effort: levels as they will apply today."""
    return {
        "default": default_id(),
        "max_budget": daily_cap,
        "models": models(),
        "levels": [
            {"id": lv.id, "label": lv.label, "blurb": lv.blurb, "model": lv.model, "budget": min(lv.budget, daily_cap)}
            for lv in LEVELS
        ],
    }
