"""Placement health — a dead local surface demotes cheap/crit work to
cloud, PII and over-budget steps block rather than crash-land, and the
pure table is unchanged by default.
"""

from services import placement
from services.placement import health


def test_cheap_demotes_when_local_down():
    p = placement.place({"tier": "cheap"}, local_up=False)
    assert p.target == "cloud-OpenRouter"
    assert "local endpoints down" in p.reason


def test_crit_demotes_when_local_down():
    p = placement.place({"latency": "crit"}, local_up=False)
    assert p.target == "cloud-OpenRouter"


def test_pii_blocks_rather_than_leak_or_crash_land():
    # Fail-closed: unapproved PII can never go cloud, and with local dead it
    # must not be sent to a corpse either — it blocks with the reason.
    p = placement.place({"tier": "frontier"}, pii_flagged=True, local_up=False)
    assert p.target == "blocked"
    assert not p.target.startswith("cloud")


def test_overbudget_blocks_when_local_down():
    p = placement.place({"tier": "mid", "est_cost": 0.5}, headroom=0.01, local_up=False)
    assert p.target == "blocked" and "raise the cap" in p.reason


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
