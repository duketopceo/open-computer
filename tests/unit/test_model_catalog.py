"""Curated model catalog (KTD14): the spend rule is enforced by the loader and by these tests. No network, no model calls."""

from __future__ import annotations

import copy
import json
import re

import pytest

from services.router import model_catalog as mc
from services.router.policy_table import TIER_MODELS

PREMIUM = re.compile(r"^(anthropic/|openai/o\d|openai/gpt-5(\.\d+)?$|google/gemini-\d(\.\d)?-pro)")
RAW = json.loads(mc.PATH.read_text())


def variant(**edits):
    """The shipped catalog with `edits` applied; a callable may mutate the copy."""
    data = copy.deepcopy(RAW)
    for fn in edits.values():
        fn(data)
    return data


def entry(data, mid):
    return next(m for m in data["models"] if m["id"] == mid)


def test_shipped_catalog_loads_and_every_price_is_under_the_ceiling_unless_flagged():
    cat = mc.load()
    for e in cat.entries:
        assert e.over_ceiling() == e.above_ceiling, e.id


def test_defaults_and_presets_are_under_the_ceiling_and_the_default_is_not_high():
    cat = mc.CATALOG
    assert cat.default_orchestrator == "particle-ai/particle-1"
    worker = cat.get(cat.default_worker)
    assert worker and "worker" in worker.roles and not worker.over_ceiling()
    for model_id in [cat.default_orchestrator, cat.default_worker, *cat.presets.values()]:
        assert not cat.get(model_id).above_ceiling
    from services.opsview import effort

    assert effort.FALLBACK_DEFAULT != "high"


def test_no_catalog_id_matches_the_premium_pattern_including_the_worker():
    assert [e.id for e in mc.CATALOG.entries if PREMIUM.match(e.id)] == []
    assert mc.DEFAULT_WORKER_MODEL == "z-ai/glm-5.3-flash"


def test_tier_models_come_from_the_catalog_and_there_is_no_local_tier():
    assert "local" not in TIER_MODELS
    for tier, ids in mc.CATALOG.tier_models().items():
        assert TIER_MODELS[tier] == ids and ids
    assert all(mc.CATALOG.get(m) for t in mc.CLOUD_TIERS for m in TIER_MODELS[t])


def test_roles_pick_the_right_lists():
    assert "z-ai/glm-5.3-flash" in [e.id for e in mc.CATALOG.workers()]
    assert "z-ai/glm-5.3-flash" not in [e.id for e in mc.CATALOG.orchestrators()]
    assert mc.CATALOG.preset("low") == "google/gemma-4-26b-a4b-it"


def test_price_over_the_ceiling_without_the_flag_is_a_load_error():
    def bump(d):
        entry(d, "deepseek/deepseek-v4-pro")["output_price"] = 4.81

    with pytest.raises(mc.CatalogError, match="above the ceiling"):
        mc.parse(variant(a=bump))


def test_flag_on_an_under_ceiling_entry_is_a_load_error():
    def flag(d):
        entry(d, "nvidia/nemotron-3.5-lightning")["above_ceiling"] = True

    with pytest.raises(mc.CatalogError, match="at or under the ceiling"):
        mc.parse(variant(a=flag))


@pytest.mark.parametrize(
    "edit,match",
    [
        (lambda d: entry(d, "z-ai/glm-5.3-flash").update(roles=["planner"]), "roles"),
        (lambda d: entry(d, "z-ai/glm-5.3-flash").pop("input_price"), "input_price"),
        (lambda d: entry(d, "nvidia/nemotron-3.5-lightning").update(tools=False), "tool calling"),
        (lambda d: d["models"].append(copy.deepcopy(entry(d, "nvidia/nemotron-3.5-lightning"))), "duplicate"),
    ],
)
def test_other_rule_breaks_are_load_errors(edit, match):
    with pytest.raises(mc.CatalogError, match=match):
        mc.parse(variant(a=edit))


@pytest.mark.parametrize("where", ["default_orchestrator", "default_worker", "preset", "tier"])
def test_a_flagged_model_cannot_be_a_default_preset_or_tier_member(where):
    """A hand-added above-ceiling entry loads, but wiring it into any default place is refused."""

    def go(d):
        d["models"].append(
            {"id": "x/big", "label": "Big", "provider": "X", "roles": ["orchestrator", "worker"], "tools": True,
             "input_price": 9.0, "output_price": 40.0, "context": 1000, "tiers": [], "above_ceiling": True}
        )
        if where == "default_orchestrator":
            d["default_orchestrator"] = "x/big"
        elif where == "default_worker":
            d["default_worker"] = "x/big"
        elif where == "preset":
            d["presets"]["high"] = "x/big"
        else:
            entry(d, "x/big")["tiers"] = ["frontier"]

    with pytest.raises(mc.CatalogError, match="above the ceiling|tier member"):
        mc.parse(variant(a=go))


def test_a_flagged_entry_added_by_hand_loads_and_shows_price_and_mark():
    def add(d):
        d["models"].append(
            {"id": "x/big", "label": "Big", "provider": "X", "roles": ["orchestrator"], "tools": True,
             "input_price": 9.0, "output_price": 40.0, "context": 1000, "tiers": [], "above_ceiling": True}
        )

    cat = mc.parse(variant(a=add))
    shown = next(e.public() for e in cat.orchestrators() if e.id == "x/big")
    assert shown["above_ceiling"] is True and shown["input_price"] == 9.0
    assert "x/big" not in {m for ms in cat.tier_models().values() for m in ms}


def test_shipped_grok_47_is_flagged_because_its_published_price_is_above_the_ceiling():
    grok = mc.CATALOG.get("x-ai/grok-4.7")
    assert grok.above_ceiling and grok.over_ceiling()
    assert "x-ai/grok-4.7" not in {m for ms in TIER_MODELS.values() for m in ms}


def test_no_local_models_even_with_the_old_flag(monkeypatch):
    monkeypatch.setenv("OC_LOCAL_MODELS", "1")
    assert mc.payload()["local"] == []


def test_engines_use_the_catalog_worker_default_not_a_hard_coded_id():
    import inspect

    from services.orchestrator import engines

    src = inspect.getsource(engines)
    assert "z-ai/glm-5.3-flash" not in src
    assert inspect.signature(engines.work_fn).parameters["or_model"].default == mc.DEFAULT_WORKER_MODEL


# --- effort levels: three models each, no overlap ---------------------------------------------------
OR_MODELS = {m["id"]: m for m in json.loads((mc.PATH.parents[2] / "tests" / "fixtures" / "openrouter" /
                                             "models_2026-11-05.json").read_text())["data"]}
# Weights not on Hugging Face; kept as the owner's chosen Medium preset and orchestrator default (PR #123).
NOT_OPEN_WEIGHT_OK = {"particle-ai/particle-1"}


def test_each_level_has_exactly_three_models_of_its_own():
    cat = mc.CATALOG
    chains = {lv: cat.level_chain(lv) for lv in mc.LEVELS}
    for lv, chain in chains.items():
        assert len(chain) == mc.TIER_SIZE == len(set(chain)), lv
        assert chain[0] == cat.preset(lv) and TIER_MODELS[mc.LEVEL_TIERS[lv]] == chain
    every = [m for chain in chains.values() for m in chain]
    assert len(every) == len(set(every)) == 9
    assert not set(cat.chain(cat.default_worker)) & set(every)


def test_level_models_are_open_weight_tools_capable_and_priced_as_recorded():
    """Checked against a recorded /api/v1/models snapshot, never the live list."""
    for e in mc.CATALOG.entries:
        rec = OR_MODELS[e.id]
        assert (e.input_price, e.output_price) == pytest.approx(
            (float(rec["pricing"]["prompt"]) * 1e6, float(rec["pricing"]["completion"]) * 1e6)), e.id
        assert e.context == rec["context_length"], e.id
        assert e.tools == ("tools" in rec["supported_parameters"]), e.id
        if e.reasoning:
            assert "reasoning" in rec["supported_parameters"], e.id
    for lv in mc.LEVELS:
        for m in mc.CATALOG.level_chain(lv):
            assert OR_MODELS[m]["hugging_face_id"] or m in NOT_OPEN_WEIGHT_OK, m
    for lv in ("medium", "high"):  # reasoning is asked on these levels; every model there accepts it
        assert all(mc.CATALOG.get(m).reasoning for m in mc.CATALOG.level_chain(lv)), lv


def _swap_fallback(d, mid, old, new):
    e = entry(d, mid)
    e["fallbacks"] = [new if f == old else f for f in e["fallbacks"]]


@pytest.mark.parametrize(
    "edit,match",
    [
        (lambda d: entry(d, "particle-ai/particle-1").update(tiers=["mid", "frontier"]), "at most one tier"),
        (lambda d: entry(d, "z-ai/glm-5.3-flash").update(tiers=["cheap"]), "exactly 3"),
        (lambda d: entry(d, "deepseek/deepseek-v4-flash").update(tiers=[]), "exactly 3"),
        (lambda d: _swap_fallback(d, "google/gemma-4-26b-a4b-it", "deepseek/deepseek-v4-flash",
                                  "particle-ai/particle-1"), "must be exactly the cheap tier"),
        (lambda d: entry(d, "google/gemma-4-26b-a4b-it").update(fallbacks=["nvidia/nemotron-3.5-lightning"]),
         "must be exactly the cheap tier"),
        (lambda d: _swap_fallback(d, "xiaomi/mimo-v2.6-pro", "deepseek/deepseek-v4-pro",
                                  "particle-ai/particle-1"), "leave its frontier tier"),
        (lambda d: entry(d, "z-ai/glm-5.3-flash").update(fallbacks=["deepseek/deepseek-v4.1-flash"]),
         "default worker chain shares"),
        (lambda d: d["presets"].update(high="particle-ai/particle-1"), "must be exactly the frontier tier"),
    ],
)
def test_overlapping_or_short_levels_refuse_to_load(edit, match):
    with pytest.raises(mc.CatalogError, match=match):
        mc.parse(variant(a=edit))


def test_models_payload_lists_each_levels_three_models():
    levels = mc.payload()["levels"]
    assert set(levels) == set(mc.LEVELS)
    assert levels["medium"] == ["particle-ai/particle-1", "deepseek/deepseek-v4.1-flash", "xiaomi/mimo-v2.6-flash"]
    assert all(len(ms) == 3 for ms in levels.values())
