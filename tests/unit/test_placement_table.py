"""OC-036: placement is a pure function; its invariants hold over the whole input cross product (1,152 cases),
and each invariant fails against a deliberately broken placer."""

from __future__ import annotations

import itertools
import json

import pytest

from services.cost import CostLedger, Usage
from services.orchestrator import Orchestrator, WorkResult
from services.placement import Placement, headroom_from, place


def cases():
    for pii, approved, latency, tier, mreq, sandbox, est, head in itertools.product(
        (False, True),
        (False, True),
        ("low", "normal", "crit"),
        ("local", "cheap", "mid", "frontier"),
        ("", "frontier"),
        (False, True),
        (0.0, 0.05),
        (None, 0.01, 1.0),
    ):
        yield (
            {"tier": tier, "latency": latency, "model_requirement": mreq, "needs_sandbox": sandbox, "est_cost": est},
            {"pii_flagged": pii, "approved": approved, "headroom": head},
        )


def cloud(p: Placement) -> bool:
    return p.target.startswith("cloud")


def violations(placer) -> list[str]:
    out = []
    for sub, kw in cases():
        p = placer(sub, **kw)
        tag = f"{sub} {kw}"
        if kw["pii_flagged"] and not kw["approved"] and cloud(p):
            out.append(f"I1 {tag} -> {p.target}")
        if p.target.startswith("local"):
            out.append(f"I0 {tag} -> {p.target}")  # no local models: every model call goes through OpenRouter
        over = (
            kw["headroom"] is not None
            and sub["est_cost"] > kw["headroom"]
            and sub["latency"] != "crit"
            and not (kw["pii_flagged"] and not kw["approved"])
        )
        # Chat over the cap must not become a cloud call. A sandbox step keeps
        # cloud-sandbox; the run holds it instead of demoting or blocking here.
        if over and not sub["needs_sandbox"] and cloud(p):
            out.append(f"I3 {tag} -> {p.target}")
        if over and sub["needs_sandbox"] and p.target != "cloud-sandbox":
            out.append(f"I3 {tag} -> {p.target}")
        if (
            cloud(p)
            and p.target == "cloud-OpenRouter"
            and (kw["pii_flagged"] or sub["tier"] == "frontier" or sub["model_requirement"] == "frontier")
            and not p.gate_required
        ):
            out.append(f"I4 {tag} ungated")
        if p != placer(sub, **kw):
            out.append(f"I5 {tag} nondeterministic")
    return out


def test_real_placer_satisfies_every_invariant_over_the_cross_product():
    assert sum(1 for _ in cases()) == 1152
    assert violations(place) == []


def always_cloud(sub, **kw):
    return Placement("cloud-OpenRouter", "broken")


def ignores_pii(sub, *, pii_flagged=False, **kw):
    return place(sub, pii_flagged=False, **kw)


def ignores_headroom(sub, *, headroom=None, **kw):
    return place(sub, headroom=None, **kw)


def ungated_pii(sub, **kw):
    p = place(sub, **kw)
    return Placement(p.target, p.reason, gate_required=False)


def goes_local(sub, **kw):
    return Placement("local-ollama", "broken")


@pytest.mark.parametrize(
    "mutant,invariant",
    [
        (always_cloud, "I1"),
        (ignores_pii, "I1"),
        (ignores_headroom, "I3"),
        (ungated_pii, "I4"),
        (goes_local, "I0"),
    ],
)
def test_each_invariant_fails_on_a_broken_placer(mutant, invariant):
    assert any(v.startswith(invariant) for v in violations(mutant))


def test_mid_tier_approved_pii_is_gated_not_just_allowed():
    p = place({"tier": "mid"}, pii_flagged=True, approved=True)
    assert p.target == "cloud-OpenRouter" and p.gate_required  # was ungated before OC-036


def test_headroom_comes_from_the_cost_ledger_of_the_run(tmp_path):
    led = CostLedger(tmp_path / "c.ndjson", default_cap=0.010)
    assert headroom_from(ledger=led, run="r") == pytest.approx(0.010)
    tok = led.check("r", 0.004)
    assert headroom_from(ledger=led, run="r") == pytest.approx(0.006)  # reservations count
    led.record("r", "s", Usage("m", 1, 1, 0.004), token=tok)
    assert headroom_from(ledger=led, run="r") == pytest.approx(0.006)
    led.record("r", "s", Usage("m", 1, 1, 0.006))
    assert headroom_from(ledger=led, run="r") == 0.0  # paused run: nothing left
    assert headroom_from() is None


def _engine(_goal):
    return json.dumps(
        [
            {"id": "a", "title": "a", "prompt": "collect", "tier": "mid", "est_cost": 0.004},
            {"id": "b", "title": "b", "prompt": "analyze", "tier": "mid", "deps": ["a"], "est_cost": 0.004},
        ]
    )


def test_dispatch_blocks_when_the_run_ledger_cannot_cover_the_estimate(tmp_path):
    led = CostLedger(tmp_path / "c.ndjson", default_cap=0.006)
    orch = Orchestrator(tmp_path / "o", ledger=led)
    orch.run("g", _engine, lambda t: WorkResult(f"did:{t['subtask']}", Usage("m", 1, 1, 0.003)))
    by = {d["subtask"]: d for d in orch.dispatches[next(iter(orch.dispatches))]}
    assert by["a"]["placement"] == "cloud-OpenRouter"  # headroom 0.006 covers 0.004
    assert by["b"]["placement"] == "blocked" and "over-budget" in by["b"]["reason"]


def test_no_step_ever_lands_on_a_local_model():
    # Cheap, latency-critical and default steps go to OpenRouter.
    # Unapproved private text holds. Over-budget chat blocks here; it is not a local model.
    for sub in ({"tier": "cheap"}, {"tier": "mid", "latency": "crit"}, {"tier": "local"}, {}):
        assert place(sub, local_up=True).target == "cloud-OpenRouter", sub
    p = place({"tier": "mid"}, pii_flagged=True, approved=False, local_up=True)
    assert p.target == "blocked" and "hold" in p.reason
    assert not p.target.startswith("local") and not p.target.startswith("cloud")
    p = place({"tier": "mid"}, headroom=0.01)
    assert p.target == "cloud-OpenRouter"  # est 0.0 fits headroom
    p = place({"tier": "mid", "est_cost": 0.5}, headroom=0.01, local_up=True)
    assert p.target == "blocked" and not p.target.startswith("local")


def test_needs_sandbox_is_a_capability_constraint_not_a_quality_pref():
    # A step that must execute can only live in the sandbox — latency demotes
    # to local or cloud-fallback are chat endpoints that cannot run code.
    p = place({"needs_sandbox": True, "latency": "crit"})
    assert p.target == "cloud-sandbox"
    # Over the cap the legal target stays the sandbox. Dispatch holds the mail.
    p = place({"needs_sandbox": True, "est_cost": 0.5}, headroom=0.01, local_up=True)
    assert p.target == "cloud-sandbox"
    assert not p.target.startswith("local")


def test_local_up_and_the_flag_never_select_a_local_target(monkeypatch):
    monkeypatch.setenv("OC_LOCAL_MODELS", "1")
    cheap = {"tier": "cheap", "est_cost": 0.0}
    assert place(cheap, local_up=True).target == "cloud-OpenRouter"
    monkeypatch.delenv("OC_LOCAL_MODELS", raising=False)
    assert place(cheap, local_up=True).target == "cloud-OpenRouter"
    assert place({"tier": "mid", "latency": "crit"}, local_up=True).target != "local-llamacpp"
    over = place({"tier": "mid", "est_cost": 0.5}, headroom=0.01, local_up=True)
    assert over.target != "local-ollama"


def test_frontier_device_egress_and_repeat_stay_put():
    frontier = place({"tier": "frontier"})
    assert frontier.target == "cloud-OpenRouter" and frontier.gate_required
    device = place({"needs_device": True, "device_root": "/tmp/x", "tier": "frontier"}, pii_flagged=True)
    assert device.target == "device"
    assert place({"tier": "cheap"}).target != "device"
    blocked = place({"needs_egress": True, "egress_hosts": ["evil.example"]}, egress_approved=())
    assert blocked.target == "blocked" and "evil.example" in blocked.reason
    args = ({"tier": "mid", "est_cost": 0.01},)
    kw = {"headroom": 1.0, "local_up": False}
    assert place(*args, **kw) == place(*args, **kw)


def test_blocked_placement_tombstones_the_subtask(tmp_path):
    # A blocked step must fail visibly (reason + hint on the audit row), never
    # reach a worker, and never leave dependents hanging.
    led = CostLedger(tmp_path / "c.ndjson", default_cap=0.001)

    orch = Orchestrator(tmp_path / "o", ledger=led)
    orch.run(
        "g",
        _engine,  # subtask a: est 0.004 > headroom 0.001 -> blocked
        lambda t: WorkResult(f"did:{t['subtask']}", Usage("m", 1, 1, 0.001)),
    )
    run_id = next(iter(orch.dispatches))
    results = orch.results(run_id)
    assert results["a"]["state"] == "FAILED" and results["b"]["state"] == "FAILED"
    fails = [r for r in orch.audit.rows() if r.get("event") == "worker_fail"]
    kinds = {f["kind"] for f in fails}
    assert "NoPlacement" in kinds and "DependencyFailed" in kinds
    no_place = next(f for f in fails if f["kind"] == "NoPlacement")
    assert "over-budget" in no_place["error"] and "raise the cap" in no_place["hint"]
