"""Placement health — a dead local surface demotes cheap/crit work to
cloud, PII still stays local, and the pure table is unchanged by default.
"""

import pytest

from services import placement
from services.placement import health


def test_cheap_demotes_when_local_down():
    p = placement.place({"tier": "cheap"}, local_up=False)
    assert p.target == "cloud-OpenRouter"
    assert "local endpoints down" in p.reason


def test_crit_demotes_when_local_down():
    p = placement.place({"latency": "crit"}, local_up=False)
    assert p.target == "cloud-OpenRouter"


def test_pii_stays_local_even_when_down():
    p = placement.place({"tier": "frontier"}, pii_flagged=True, local_up=False)
    assert p.target.startswith("local")  # fail-closed beats a leak


def test_default_table_unchanged():
    assert placement.place({"tier": "cheap"}).target == "local-llamacpp"


def test_place_live_injects_probe(monkeypatch):
    monkeypatch.setattr(health, "_cache", (0.0, False))
    monkeypatch.setattr(health, "_probe", lambda timeout=1.0: False)
    p = placement.place_live({"tier": "cheap"})
    assert p.target == "cloud-OpenRouter"
    monkeypatch.setattr(health, "_cache", (0.0, True))
    monkeypatch.setattr(health, "_probe", lambda timeout=1.0: True)
    p = placement.place_live({"tier": "cheap"})
    assert p.target == "local-llamacpp"
