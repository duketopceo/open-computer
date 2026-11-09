"""Spend rule: no premium model is a default (ROADMAP.md, Spend rule)."""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.router.policy_table import TIER_MODELS  # noqa: E402

PREMIUM = re.compile(r"^(anthropic/|openai/o\d|openai/gpt-5(\.\d+)?$|google/gemini-\d(\.\d)?-pro)")


def test_no_premium_model_in_any_tier():
    offenders = [m for models in TIER_MODELS.values() for m in models if PREMIUM.match(m)]
    assert offenders == []


def test_every_tier_has_a_candidate():
    assert all(TIER_MODELS[t] for t in ("cheap", "mid", "frontier"))


def test_docs_name_no_premium_orchestrator():
    root = Path(__file__).resolve().parents[2]
    text = (root / "ARCHITECTURE_MAP.md").read_text()
    assert "Opus" not in text


def test_default_orchestrator_model_is_not_premium():
    from services.agents.loop import DEFAULT_ORCHESTRATOR_MODEL

    assert DEFAULT_ORCHESTRATOR_MODEL == "particle-ai/particle-1"
    assert not PREMIUM.match(DEFAULT_ORCHESTRATOR_MODEL)


def test_no_effort_level_or_override_is_premium_and_default_is_not_the_top():
    from services.opsview import effort

    assert [m for m in effort.models() if PREMIUM.match(m)] == []
    assert [lv.id for lv in effort.LEVELS if PREMIUM.match(lv.model)] == []
    assert effort.FALLBACK_DEFAULT != effort.LEVELS[-1].id


def test_catalog_entries_are_not_premium_and_flagged_ones_are_never_a_default_place():
    from services.opsview import effort
    from services.router import model_catalog as mc
    from services.orchestrator import engines
    from services.agents.loop import DEFAULT_ORCHESTRATOR_MODEL

    cat = mc.CATALOG
    assert [e.id for e in cat.entries if PREMIUM.match(e.id)] == []  # covers z-ai/glm-5.3-flash (P16)
    flagged = {e.id for e in cat.entries if e.above_ceiling}
    in_default_places = {
        DEFAULT_ORCHESTRATOR_MODEL,
        mc.DEFAULT_WORKER_MODEL,
        engines.work_fn.__defaults__[0],
        *cat.presets.values(),
        *(lv.model for lv in effort.LEVELS),
        *effort.models(),
        *(m for ms in TIER_MODELS.values() for m in ms),
    }
    assert flagged & in_default_places == set()
