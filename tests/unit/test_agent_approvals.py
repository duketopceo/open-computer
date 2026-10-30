"""Console approval store, approver and job lease. Fake clock + injected sleep: nothing here waits."""

from __future__ import annotations

import json
import threading

import pytest

from services.agents.approvals import ApprovalError, ApprovalStore, ConsoleApprover, lease
from services.gate.gate import payload_hash


class Clock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    return ApprovalStore(tmp_path / "approvals.ndjson", ttl_s=100, clock=clock)


def _h(job, kind, detail):
    return payload_hash({"job": job, "kind": kind, "detail": detail})


def _approver_thread(ap, kind, detail):
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("r", ap(kind, detail)), daemon=True)
    t.start()
    return t, out


def _wait_pending(store, n=1, job=None):
    done = threading.Event()
    for _ in range(2000):
        if len(store.pending(job)) >= n:
            done.set()
            break
        threading.Event().wait(0.001)
    assert done.is_set()
    return store.pending(job)


def _approver(store, job="j1", **kw):
    return ConsoleApprover(store, job, poll_s=0.001, **kw)


def test_missing_store_creates_nothing(tmp_path, clock):
    p = tmp_path / "sub" / "a.ndjson"
    s = ApprovalStore(p, clock=clock)
    assert s.pending() == [] and s.get("ag_00000000") is None
    assert not p.exists() and not p.parent.exists()


def test_approve_with_hash_releases_waiter(store):
    t, out = _approver_thread(_approver(store), "plan", {"steps": ["a"]})
    (req,) = _wait_pending(store)
    assert req["id"].startswith("ag_") and len(req["id"]) == 11
    assert req["hash"] == _h("j1", "plan", {"steps": ["a"]})
    assert req["expires_at"] == 1100.0
    store.decide(req["id"], True, "owner", req["hash"])
    t.join(2)
    assert out["r"] is True
    assert store.get(req["id"])["consumed"] is True
    assert store.pending() == []


def test_deny_returns_false(store):
    t, out = _approver_thread(_approver(store), "risky", {"action": "push"})
    (req,) = _wait_pending(store)
    store.decide(req["id"], False, "owner", req["hash"], note="no")
    t.join(2)
    assert out["r"] is False
    assert store.get(req["id"])["status"] == "denied"


def test_stale_hash_raises_409_and_leaves_pending(store):
    t, out = _approver_thread(_approver(store), "plan", {"steps": ["a"]})
    (req,) = _wait_pending(store)
    with pytest.raises(ApprovalError) as e:
        store.decide(req["id"], True, "owner", "0" * 64)
    assert e.value.status == 409
    assert [r["id"] for r in store.pending()] == [req["id"]]
    store.decide(req["id"], False, "owner", req["hash"])
    t.join(2)


def test_unknown_id_404_and_bad_input_422(store):
    with pytest.raises(ApprovalError) as e:
        store.decide("ag_deadbeef", True, "owner", "x")
    assert e.value.status == 404
    rid = store.open("j1", "plan", {"s": 1})
    for kw in ({"by": "", "hash": "h"}, {"by": "owner", "hash": ""}, {"by": "owner", "hash": 5}):
        with pytest.raises(ApprovalError) as e:
            store.decide(rid, True, **kw)
        assert e.value.status == 422
    assert store.get(rid)["status"] == "pending"


def test_second_decision_refused(store):
    rid = store.open("j1", "plan", {"s": 1})
    h = _h("j1", "plan", {"s": 1})
    store.decide(rid, True, "owner", h)
    with pytest.raises(ApprovalError) as e:
        store.decide(rid, False, "owner", h)
    assert e.value.status == 409
    assert store.get(rid)["status"] == "approved"


def test_tampered_store_hash_rejected(tmp_path, clock):
    p = tmp_path / "a.ndjson"
    s = ApprovalStore(p, ttl_s=100, clock=clock)
    rid = s.open("j1", "egress", {"host": "good.example.com"})
    h = _h("j1", "egress", {"host": "good.example.com"})
    rows = [json.loads(x) for x in p.read_text().splitlines()]
    rows[0]["detail"] = {"host": "evil.example.com"}  # payload edited, hash left alone
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    for fn in (lambda: s.decide(rid, True, "owner", h), lambda: s.verify(rid, h)):
        with pytest.raises(ApprovalError) as e:
            fn()
        assert e.value.status == 409
    assert s.get(rid)["status"] == "pending"


def test_verify_is_read_only(store, tmp_path):
    rid = store.open("j1", "egress", {"host": "a.example.com"})
    h = _h("j1", "egress", {"host": "a.example.com"})
    before = store.path.read_text()
    rec = store.verify(rid, h)
    assert rec["id"] == rid and store.path.read_text() == before
    with pytest.raises(ApprovalError) as e:
        store.verify(rid, "bad")
    assert e.value.status == 409
    with pytest.raises(ApprovalError) as e:
        store.verify("ag_00000000", h)
    assert e.value.status == 404
    store.decide(rid, True, "owner", h)
    with pytest.raises(ApprovalError) as e:  # decided: no second side effect
        store.verify(rid, h)
    assert e.value.status == 409


def test_ttl_expiry_denies_and_records_expired(store, clock):
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        clock.t += 60  # each poll advances time; 2nd poll passes the 100s TTL

    ap = ConsoleApprover(store, "j1", poll_s=5, ttl_s=100, clock=clock, sleep=sleep)
    assert ap("plan", {"s": 1}) is False
    (rec,) = [r for r in store.items()]
    assert rec["status"] == "expired" and rec["consumed"] is True
    assert sleeps


def test_expired_rejected_by_decide_before_approver_notices(store, clock):
    rid = store.open("j1", "plan", {"s": 1})
    h = _h("j1", "plan", {"s": 1})
    clock.t += 101
    assert store.pending() == []  # never shown as actionable
    assert store.get(rid)["status"] == "expired"
    with pytest.raises(ApprovalError) as e:
        store.decide(rid, True, "owner", h)
    assert e.value.status == 409
    with pytest.raises(ApprovalError):
        store.verify(rid, h)


def test_reattach_one_request_row(store):
    detail = {"steps": ["a"]}
    rid = store.open("j1", "plan", detail)
    t, out = _approver_thread(_approver(store), "plan", detail)
    _wait_pending(store)
    assert len(store.items()) == 1 and store.items()[0]["id"] == rid
    store.decide(rid, True, "owner", _h("j1", "plan", detail))
    t.join(2)
    assert out["r"] is True
    assert sum(1 for x in store.path.read_text().splitlines() if json.loads(x)["event"] == "request") == 1


def test_decision_before_approver_is_honored_and_consumed_once(store):
    detail = {"steps": ["a"]}
    rid = store.open("j1", "plan", detail)
    store.decide(rid, True, "owner", _h("j1", "plan", detail))
    assert _approver(store)("plan", detail) is True
    assert store.get(rid)["consumed"] is True
    # consumed: a later ask with the same payload is a NEW request, not a replay of the old approval
    t, out = _approver_thread(_approver(store), "plan", detail)
    (req,) = _wait_pending(store)
    assert req["id"] != rid
    store.decide(req["id"], False, "owner", req["hash"])
    t.join(2)
    assert out["r"] is False
    assert sum(1 for x in store.path.read_text().splitlines() if json.loads(x)["event"] == "consume") == 2


def test_changed_detail_is_new_request_old_not_approvable_for_it(store):
    old = store.open("j1", "plan", {"steps": ["a"]})
    t, out = _approver_thread(_approver(store), "plan", {"steps": ["a", "b"]})
    reqs = _wait_pending(store, 2)
    new = next(r for r in reqs if r["id"] != old)
    assert new["hash"] != _h("j1", "plan", {"steps": ["a"]})
    with pytest.raises(ApprovalError) as e:
        store.decide(old, True, "owner", new["hash"])
    assert e.value.status == 409
    store.decide(new["id"], True, "owner", new["hash"])
    t.join(2)
    assert out["r"] is True and store.get(old)["status"] == "pending"


def test_concurrent_asks_each_get_a_decision(store):
    n = 6
    results = {}
    threads = []
    for i in range(n):
        t = threading.Thread(target=lambda i=i: results.__setitem__(i, _approver(store)("tool", {"i": i})), daemon=True)
        t.start()
        threads.append(t)
    reqs = _wait_pending(store, n)
    assert len({r["id"] for r in reqs}) == n
    for r in reqs:
        store.decide(r["id"], r["detail"]["i"] % 2 == 0, "owner", r["hash"])
    for t in threads:
        t.join(2)
    assert results == {i: i % 2 == 0 for i in range(n)}


def test_void_unresolved_keeps_plan(store):
    ids = {k: store.open("j1", k, {"k": k}) for k in ("plan", "risky", "spend", "egress", "tool")}
    other = store.open("j2", "risky", {"k": "x"})
    voided = store.void_unresolved("j1")
    assert sorted(voided) == sorted(ids[k] for k in ("risky", "spend", "egress", "tool"))
    for k in ("risky", "spend", "egress", "tool"):
        assert store.get(ids[k])["status"] == "expired"
        with pytest.raises(ApprovalError) as e:
            store.decide(ids[k], True, "owner", _h("j1", k, {"k": k}))
        assert e.value.status == 409
    assert store.get(ids["plan"])["status"] == "pending"
    assert store.get(other)["status"] == "pending"
    # a voided request is never re-attached: the new round asks afresh
    t, out = _approver_thread(_approver(store), "risky", {"k": "risky"})
    reqs = _wait_pending(store, 2, job="j1")
    req = next(r for r in reqs if r["kind"] == "risky")
    assert req["id"] != ids["risky"] and len(reqs) == 2
    store.decide(req["id"], True, "owner", req["hash"])
    t.join(2)


def test_reattach_restarts_ttl_clock(store, clock):
    detail = {"steps": ["a"]}
    rid = store.open("j1", "plan", detail)
    clock.t += 5000  # long downtime: lazily expired, but no decision recorded
    assert store.get(rid)["status"] == "expired"
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("r", _approver(store)("plan", detail)), daemon=True)
    t.start()
    (req,) = _wait_pending(store)
    assert req["id"] == rid and req["expires_at"] == clock.t + 100
    store.decide(rid, True, "owner", req["hash"])
    t.join(2)
    assert out["r"] is True


def test_default_ttl_from_env(tmp_path, monkeypatch):
    monkeypatch.delenv("OC_AGENT_APPROVAL_TTL_S", raising=False)
    assert ApprovalStore(tmp_path / "a").ttl_s == 86400
    monkeypatch.setenv("OC_AGENT_APPROVAL_TTL_S", "30")
    assert ApprovalStore(tmp_path / "a").ttl_s == 30
    monkeypatch.setenv("OC_AGENT_APPROVAL_TTL_S", "junk")
    assert ApprovalStore(tmp_path / "a").ttl_s == 86400


def test_lease_exclusive_then_released(tmp_path):
    p = tmp_path / "locks" / "job.lock"
    with lease(p) as first:
        assert first is True
        with lease(p) as second:
            assert second is False
    with lease(p) as third:
        assert third is True
