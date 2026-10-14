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
    assert all(TIER_MODELS[t] for t in ("local", "cheap", "mid", "frontier"))


def test_docs_name_no_premium_orchestrator():
    root = Path(__file__).resolve().parents[2]
    text = (root / "ARCHITECTURE_MAP.md").read_text()
    assert "Opus" not in text
