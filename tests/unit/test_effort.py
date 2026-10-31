"""Effort levels: one pick chooses the model and the per-run budget, inside the spend rule."""

from __future__ import annotations

import pytest

from services.opsview import effort
from services.router.policy_table import TIER_MODELS, TIERS


def test_levels_map_to_router_tiers_and_their_first_model():
    assert [lv.id for lv in effort.LEVELS] == ["low", "medium", "high"]
    for lv in effort.LEVELS:
        assert lv.tier in TIERS
        assert lv.model == TIER_MODELS[lv.tier][0]


def test_overrides_are_only_models_the_router_already_allows():
    assert set(effort.models()) <= {m for ms in TIER_MODELS.values() for m in ms}


def test_budgets_rise_with_effort():
    budgets = [lv.budget for lv in effort.LEVELS]
    assert budgets == sorted(budgets) and len(set(budgets)) == 3


def test_default_is_never_high(monkeypatch):
    monkeypatch.delenv("OC_EFFORT_DEFAULT", raising=False)
    assert effort.default_id() == "low"
    monkeypatch.setenv("OC_EFFORT_DEFAULT", "nonsense")
    assert effort.default_id() == "low"
    monkeypatch.setenv("OC_EFFORT_DEFAULT", "Medium")
    assert effort.default_id() == "medium"


def test_resolve_defaults_and_levels(monkeypatch):
    monkeypatch.delenv("OC_EFFORT_DEFAULT", raising=False)
    assert effort.resolve(None, None, None, 1.0) == effort.Plan("low", effort.BY_ID["low"].model, 0.10)
    p = effort.resolve("medium", "", "", 1.0)
    assert (p.effort, p.model, p.budget) == ("medium", effort.BY_ID["medium"].model, 0.30)


def test_the_daily_cap_clamps_every_level():
    assert effort.resolve("high", None, None, 0.5).budget == 0.5
    assert effort.resolve("low", None, None, 0.05).budget == 0.05
    cat = effort.catalog(0.5)
    assert [lv["budget"] for lv in cat["levels"]] == [0.10, 0.30, 0.5] and cat["max_budget"] == 0.5


def test_advanced_override_picks_an_allowed_model_and_a_budget_within_the_cap():
    m = effort.models()[-1]
    p = effort.resolve("low", m, "0.25", 1.0)
    assert (p.effort, p.model, p.budget) == ("low", m, 0.25)


@pytest.mark.parametrize(
    "args",
    [
        ("ultra", None, None),
        (3, None, None),
        ("low", "anthropic/claude-opus-4", None),
        ("low", ["x"], None),
        ("low", None, "abc"),
        ("low", None, "0"),
        ("low", None, "-1"),
        ("low", None, "nan"),
        ("low", None, "inf"),
        ("low", None, True),
        ("low", None, "1.01"),
    ],
)
def test_resolve_refuses_unknown_premium_and_over_cap(args):
    with pytest.raises(ValueError):
        effort.resolve(*args, 1.0)
