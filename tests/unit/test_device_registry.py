"""Plan P1: device registry, signed step envelopes, `device` placement target, dispatch mailbox routing."""

from __future__ import annotations

import json

import pytest

from services.device import DeviceRegistry, EnvelopeError, mint_secret, open_envelope, seal
from services.device_agent import execute, heartbeat_payload, registration_payload
from services.orchestrator.dispatch import PARKED, device_mailbox, dispatch
from services.placement import TARGETS, Placement, place
from tests.unit.test_placement_table import cases, violations

T0 = 1_700_000_000.0


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


# ── registry ──────────────────────────────────────────────────────────


def test_register_is_idempotent_and_persists(tmp_path):
    clk = Clock()
    reg = DeviceRegistry(tmp_path, clock=clk)
    d = reg.register(registration_payload("mac-mini", ["/Users/k/Projects", "/Users/k/Projects"], ["fs", "bogus"]))
    assert d.roots == ["/Users/k/Projects"] and d.capabilities == ["fs"]
    clk.t += 5
    d2 = reg.register(registration_payload("mac-mini", ["/Users/k/Docs"], ["fs", "browser"]))
    assert d2.registered_at == T0 and d2.last_heartbeat == T0 + 5
    assert d2.roots == ["/Users/k/Docs"] and d2.capabilities == ["browser", "fs"]
    assert len(reg.eligible()) == 1
    # a second process reading the same file sees the same device
    assert DeviceRegistry(tmp_path, clock=clk).get("mac-mini").roots == ["/Users/k/Docs"]
    assert json.loads((tmp_path / "devices.json").read_text())["mac-mini"]["os"]


def test_stale_devices_leave_placement_and_come_back_on_heartbeat(tmp_path):
    clk = Clock()
    reg = DeviceRegistry(tmp_path, clock=clk)
    reg.register(registration_payload("a", []))
    reg.register(registration_payload("b", [], ["fs", "browser"]))
    assert [d.device_id for d in reg.eligible()] == ["a", "b"]
    assert [d.device_id for d in reg.eligible(capability="browser")] == ["b"]
    clk.t += 30
    reg.heartbeat("a")
    clk.t += 45  # b last beat 75 s ago (> 60), a 45 s ago
    assert [d.device_id for d in reg.eligible()] == ["a"]
    assert reg.get("b").online is False
    reg.heartbeat("b")
    assert [d.device_id for d in reg.eligible()] == ["a", "b"]
    reg.heartbeat("a", awake=False)  # asleep machines take no steps
    assert [d.device_id for d in reg.eligible()] == ["b"]
    with pytest.raises(KeyError):
        reg.heartbeat("never-registered")
    assert heartbeat_payload("a", now=T0)["ts"] == T0


# ── envelopes ─────────────────────────────────────────────────────────


def test_envelope_rejects_tamper_replay_and_skew():
    secret = mint_secret()
    step = {"kind": "echo", "text": "hi"}
    env = seal(step, secret, now=T0)
    seen: set[str] = set()
    assert open_envelope(env, secret, now=T0 + 1, seen=seen) == step
    with pytest.raises(EnvelopeError, match="replayed"):
        open_envelope(env, secret, now=T0 + 2, seen=seen)
    tampered = {**env, "step": {"kind": "shell", "cmd": "rm -rf /"}}
    with pytest.raises(EnvelopeError, match="bad signature"):
        open_envelope(tampered, secret, now=T0 + 1)
    with pytest.raises(EnvelopeError, match="bad signature"):
        open_envelope(env, mint_secret(), now=T0 + 1)
    with pytest.raises(EnvelopeError, match="outside window"):
        open_envelope(env, secret, now=T0 + 1_000)
    with pytest.raises(EnvelopeError, match="malformed"):
        open_envelope({"step": step}, secret, now=T0)


def test_agent_executes_echo_only_and_refuses_bad_envelopes():
    secret = mint_secret()
    ok = execute(seal({"kind": "echo", "text": "ping"}, secret, now=T0), secret, now=T0)
    assert ok["ok"] and ok["echo"] == "ping" and ok["host"]
    nope = execute(seal({"kind": "shell", "cmd": "id"}, secret, now=T0), secret, now=T0)
    assert not nope["ok"] and "unsupported" in nope["error"]
    forged = execute(seal({"kind": "echo"}, "wrong-secret", now=T0), secret, now=T0)
    assert not forged["ok"] and "refused" in forged["error"]


# ── placement ─────────────────────────────────────────────────────────


def test_device_target_only_for_device_bound_steps():
    assert "device" in TARGETS
    assert violations(place) == []  # the 1,152-case table still holds (no case sets a device need)
    for sub, kw in cases():
        assert place(sub, **kw).target != "device"
    for key in ("device_root", "needs_local_app", "needs_local_browser"):
        for sub, kw in cases():
            p = place({**sub, key: True}, **kw)
            assert p.target == "device", (key, sub, kw, p)
            assert not p.gate_required
    parked = place({"device_root": "/Users/k/Projects"}, device_available=False)
    assert parked.target == "device" and "parks" in parked.reason
    live = place({"device_root": "/Users/k/Projects"}, pii_flagged=True)
    assert live == Placement("device", "device-required - runs on the user's registered machine")


# ── dispatch ──────────────────────────────────────────────────────────


class _Decision:
    route = "local"
    engine = "echo"
    tier = "cheap"
    cost = 0.0


class _Router:
    budget = None

    def route(self, prompt):
        return _Decision()


class _Scan:
    categories: tuple = ()


class _Gate:
    def scan(self, prompt):
        return _Scan()


class _Audit(list):
    def append(self, rec):
        super().append(rec)


def _fs(tmp_path, clk):
    from oc.fsproto import adapter

    return adapter(tmp_path / "run", clock=clk)


def test_dispatch_routes_device_steps_to_the_device_mailbox_or_parks(tmp_path):
    clk = Clock()
    fs = _fs(tmp_path, clk)
    reg = DeviceRegistry(tmp_path / "devices", clock=clk)
    audit = _Audit()
    sub = {"id": "s1", "title": "read notes", "prompt": "summarise my notes", "device_root": "/Users/k/Notes"}

    parked = dispatch(fs, _Router(), _Gate(), place, audit, "run1", sub, devices=reg)
    assert parked["placement"] == "device" and parked["mailbox"] == PARKED and "parks" in parked["reason"]
    assert fs.inbox_ids(PARKED) == [parked["task"]]

    reg.register(registration_payload("mac-mini", ["/Users/k/Notes"]))
    sent = dispatch(fs, _Router(), _Gate(), place, audit, "run1", {**sub, "id": "s2"}, devices=reg)
    assert sent["mailbox"] == device_mailbox("mac-mini")
    assert fs.inbox_ids("device/mac-mini") == [sent["task"]]
    body = json.loads(fs.task_body(sent["task"]))
    assert body["device_root"] == "/Users/k/Notes" and body["placement"] == "device"

    plain = dispatch(
        fs, _Router(), _Gate(), place, audit, "run1", {"id": "s3", "title": "t", "prompt": "p"}, devices=reg
    )
    assert plain["mailbox"] == "worker" and plain["placement"] != "device"
    assert fs.inbox_ids("worker") == [plain["task"]]
    assert len(audit) == 3
