"""Live placement: place_live() adds the approved egress set and never yields a local model."""

from services import placement
from services.placement import health


def test_place_live_sends_cheap_and_crit_work_to_openrouter(monkeypatch):
    monkeypatch.setattr(health, "approved_egress", lambda policy_path=None: ())
    assert placement.place_live({"tier": "cheap"}).target == "cloud-OpenRouter"
    assert placement.place_live({"tier": "mid", "latency": "crit"}).target == "cloud-OpenRouter"


def test_place_live_injects_approved_egress(monkeypatch):
    monkeypatch.setattr(health, "approved_egress", lambda policy_path=None: ("example.com",))
    p = placement.place_live({"needs_egress": True, "egress_hosts": ["example.com"]})
    assert p.target == "cloud-sandbox" and p.egress_hosts == ("example.com",)


def test_pii_and_overbudget_block_instead_of_going_local():
    p = placement.place({"tier": "frontier"}, pii_flagged=True)
    assert p.target == "blocked" and "hold" in p.reason
    p = placement.place({"tier": "mid", "est_cost": 0.5}, headroom=0.01)
    assert p.target == "blocked" and "raise the cap" in p.reason
