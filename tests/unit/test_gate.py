"""Real Gate: approvals are bound, single-use, expiring and unforgeable.

Mutation-style: each mutant subclasses the REAL Gate and breaks exactly one rule; the conformance
check for that rule must fail on the mutant and pass on the real class.
"""

from __future__ import annotations

import threading

import pytest

from checks import gate_checks as gc
from harness import CheckFailed
from services.gate import Approval, CallResult, Gate, payload_hash

A = gc.PAYLOAD_A
B = gc.PAYLOAD_B


class HashBlind(Gate):
    def _hash_matches(self, row, payload):
        return True


class Replayable(Gate):
    def _is_consumed(self, approval_id):
        return False


class Immortal(Gate):
    def _is_expired(self, row):
        return False


class Forgeable(Gate):
    def _refusal(self, payload, approval):
        return None if approval is not None else "unapproved"


class SpendsWithoutSending(Gate):
    """Burns the approval and claims success although no egress exists."""

    def request_cloud_call(self, payload, approval=None, *, sender=None):
        if sender is None and approval is not None:
            self.audit.append({"event": "cloud_call_allowed", "approval_id": approval.id})
            return CallResult(True, len(str(payload)))
        return super().request_cloud_call(payload, approval, sender=sender)


MUTANTS = [
    (gc.approval_bound_to_payload, HashBlind),
    (gc.approval_single_use, Replayable),
    (gc.approval_expires, Immortal),
    (gc.unapproved_cloud_call_blocked, Forgeable),
    (gc.no_sender_fails_closed, SpendsWithoutSending),
]


@pytest.fixture
def gate(tmp_path):
    return Gate(tmp_path / "audit.ndjson", clock=lambda: 1000.0)


@pytest.mark.parametrize("check_fn,mutant", MUTANTS, ids=lambda x: getattr(x, "__name__", str(x)))
def test_each_check_fails_on_its_real_code_mutant(check_fn, mutant, tmp_path):
    with pytest.raises(CheckFailed):
        check_fn(mutant(tmp_path / "m.ndjson"))
    check_fn(Gate(tmp_path / "ok.ndjson"))  # and passes on the correct class


def test_approval_survives_restart_but_so_does_consumption(tmp_path):
    path = tmp_path / "a.ndjson"
    appr = Gate(path).grant(A)
    sent = []
    assert Gate(path).request_cloud_call(A, appr, sender=lambda p: sent.append(p) or 1).allowed
    assert not Gate(path).request_cloud_call(A, appr, sender=lambda p: sent.append(p) or 1).allowed
    assert len(sent) == 1  # replay across a fresh process image is refused


def test_concurrent_presentations_send_exactly_once(tmp_path):
    path = tmp_path / "a.ndjson"
    appr = Gate(path).grant(A)
    sent, lock, results = [], threading.Lock(), []

    def sender(p):
        with lock:
            sent.append(p)
        return 1

    def go():
        results.append(Gate(path).request_cloud_call(A, appr, sender=sender).allowed)

    ts = [threading.Thread(target=go) for _ in range(16)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(sent) == 1 and results.count(True) == 1


def test_sender_crash_still_spends_the_approval(gate):
    appr = gate.grant(A)

    def boom(p):
        raise RuntimeError("network died mid-send")

    res = gate.request_cloud_call(A, appr, sender=boom)
    assert res.allowed and res.bytes_sent == 0 and res.reason == "sender_error"
    spy = gc.Spy()
    assert not gate.request_cloud_call(A, appr, sender=spy).allowed and spy.sent == []
    events = [r["event"] for r in gate.audit.rows()]
    assert events == ["human_decision", "cloud_call_allowed", "cloud_call_failed", "cloud_call_blocked"]


def test_a_decision_is_made_once_per_id(gate):
    gate.grant(A, approval_id="ap_1")
    with pytest.raises(ValueError):
        gate.grant(B, approval_id="ap_1")
    with pytest.raises(ValueError):
        gate.deny(A, approval_id="ap_1")


def test_sent_back_approval_cannot_release(gate):
    gate.deny(A, approval_id="ap_9", note="redact first")
    spy = gc.Spy()
    res = gate.request_cloud_call(A, gate.approval_for(A, "ap_9"), sender=spy)
    assert not res.allowed and res.reason == "unknown_approval" and spy.sent == []


def test_approval_hash_field_is_ignored_the_log_decides(gate):
    gate.grant(A, approval_id="ap_2")
    lying = Approval(id="ap_2", payload_hash=payload_hash(B))  # claims to cover B
    spy = gc.Spy()
    assert not gate.request_cloud_call(B, lying, sender=spy).allowed and spy.sent == []


STATES = ["none", "valid", "replayed", "other_payload", "expired", "forged"]


@pytest.mark.parametrize("pii", [False, True])
@pytest.mark.parametrize("state", STATES)
def test_decision_table(tmp_path, state, pii):
    payload = gc.PAYLOAD_PII if pii else A
    gate = Gate(tmp_path / "a.ndjson", clock=lambda: 1000.0)
    spy = gc.Spy()
    appr = gate.grant(payload)
    presented, target = appr, payload
    if state == "none":
        presented = None
    elif state == "replayed":
        gate.request_cloud_call(payload, appr, sender=lambda p: 1)
    elif state == "other_payload":
        target = B
    elif state == "expired":
        gate.clock = lambda: 1000.0 + gate.approval_ttl + 1
    elif state == "forged":
        presented = gate.approval_for(payload, "ap_nope")
    res = gate.request_cloud_call(target, presented, sender=spy)
    assert res.allowed == (state == "valid")
    assert len(spy.sent) == (1 if state == "valid" else 0)
    assert res.bytes_sent == (len(str(payload)) if state == "valid" else 0)
    last = gate.audit.rows()[-1]
    assert last["event"] == ("cloud_call_allowed" if state == "valid" else "cloud_call_blocked")
    if state == "valid" and pii:
        assert {"email", "ssn"} <= set(last["pii_categories"])
