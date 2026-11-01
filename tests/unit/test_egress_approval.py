"""R4: a run whose plan needs unapproved hosts raises ONE approval up front, holds those steps as
AWAITING-APPROVAL (never FAILED), and runs them once a human approves. Real queue, policy file and fsproto
tree on tmp_path; only the planner and the worker are stubs."""

from __future__ import annotations

import json

import pytest

from services.gate.gate import Gate
from services.gate.queue import ApprovalQueue, QueueError
from services.opsview import goalruns
from services.opsview.agentgrants import GrantError, grant_run_hosts
from services.orchestrator import Orchestrator
from services.orchestrator.egress import EgressApproval
from services.placement import health
from services.placement.rules import place


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("OC_EXECPLANE_DIR", str(tmp_path / "exec"))
    monkeypatch.setenv("OC_APPROVALS", str(tmp_path / "approvals.ndjson"))
    monkeypatch.setenv("OC_GOALS_DIR", str(tmp_path / "goals"))
    monkeypatch.delenv("OC_EGRESS_ALLOW", raising=False)
    monkeypatch.setenv("OC_EGRESS_APPROVAL_WAIT_S", "5")  # a test run never leaves a driver thread waiting a day
    return tmp_path


def _queue(tmp_path) -> ApprovalQueue:
    return ApprovalQueue(tmp_path / "approvals.ndjson", Gate(tmp_path / "gate-audit.ndjson"))


def _place(sub, **kw):
    return place(sub, egress_approved=health.approved_egress(), **kw)


def _plan(*steps) -> str:
    return json.dumps(list(steps))


def _step(sid, hosts, deps=()):
    return {"id": sid, "title": f"step {sid}", "prompt": f"do {sid}", "deps": list(deps),
            "needs_egress": bool(hosts), "egress_hosts": list(hosts)}


def _orch(env, name="run"):
    root = env / name
    root.mkdir(parents=True, exist_ok=True)
    egress = EgressApproval(_queue(env), health.approved_egress)
    return Orchestrator(root, place_fn=_place, egress=egress), egress


def _approve(env, item):
    """What the console does on Approve: the grant runs first, then the queue records the decision."""
    _queue(env).decide(item["id"], approve=True, by="luke@test", expect_hash=item["payload_hash"],
                       before_approve=grant_run_hosts)


def _pending(env):
    return _queue(env).pending()


def test_one_approval_lists_every_unapproved_host_across_steps(env):
    o, _ = _orch(env)
    plan = _plan(_step("a", ["kaggle.com", "datasets.example.org"]), _step("b", ["kaggle.com"], ["a"]),
                 _step("c", ["github.com"]))
    rid = o.submit("g", lambda p: plan)
    items = _pending(env)
    assert len(items) == 1  # ONE card for the whole run
    assert items[0]["payload"]["hosts"] == ["datasets.example.org", "kaggle.com"]  # github.com is a default
    assert items[0]["session"] == rid and "datasets.example.org" in items[0]["what"]
    audit = [r for r in o.audit.rows() if r["event"] == "egress_approval_requested"]
    assert audit[0]["approval"] == items[0]["id"]


def test_held_step_waits_instead_of_failing_and_unrelated_step_runs(env):
    o, _ = _orch(env)
    rid = o.submit("g", lambda p: _plan(_step("a", ["kaggle.com"]), _step("c", ["github.com"])))
    sent = {d["subtask"]: d for d in o.dispatches[rid]}
    assert set(sent) == {"c"} and sent["c"]["placement"] == "cloud-sandbox"
    assert not [r for r in o.audit.rows() if r["event"] == "worker_fail"]
    assert o.egress_pending(rid) == ["kaggle.com"]


def test_run_view_shows_waiting_for_approval_not_failed(env):
    rid = goalruns.submit("g", lambda p: _plan(_step("a", ["kaggle.com"])), lambda t: "out", by="t")
    try:
        got = goalruns.get_run(rid)
        (sub,) = got["subtasks"]
        assert sub["state"] == "AWAITING-APPROVAL" and "kaggle.com" in sub["reason"]
        assert sub["approval"] == _pending(env)[0]["id"] and sub["hosts"] == ["kaggle.com"]
        assert got["worker_failures"] == []
        row = next(r for r in goalruns.list_runs() if r["run"] == rid)
        assert row["states"]["AWAITING-APPROVAL"] == 1 and row["states"]["FAILED"] == 0
    finally:
        _approve(env, _pending(env)[0])  # lets the drive thread finish
        goalruns._live.pop(rid, None)


def test_approving_grants_the_hosts_and_the_held_steps_run(env):
    o, _ = _orch(env)
    rid = o.submit("g", lambda p: _plan(_step("a", ["kaggle.com"]), _step("b", [], ["a"])))
    assert o.dispatches[rid] == []
    _approve(env, _pending(env)[0])
    sleeps = []
    assert o.wait_for_egress(rid, sleep=sleeps.append) is True and sleeps == []  # decided before the call: no waiting, still dispatched
    assert [d["subtask"] for d in o.dispatches[rid]] == ["a"]
    assert o.dispatches[rid][0]["placement"] == "cloud-sandbox"
    o.work(lambda t: f"out:{t['subtask']}")  # worker finishes a, which unblocks b
    o.work(lambda t: f"out:{t['subtask']}")
    res = o.results(rid)
    assert {k: v["state"] for k, v in res.items()} == {"a": "DONE", "b": "DONE"}


def test_wait_for_egress_blocks_until_decided(env):
    o, _ = _orch(env)
    rid = o.submit("g", lambda p: _plan(_step("a", ["kaggle.com"])))
    item = _pending(env)[0]
    polls = []

    def sleep(_):
        polls.append(1)
        if len(polls) == 3:
            _approve(env, item)  # the human decides while the run waits

    assert o.wait_for_egress(rid, sleep=sleep) is True and len(polls) == 3
    assert [d["subtask"] for d in o.dispatches[rid]] == ["a"]


def test_wait_for_egress_gives_up_at_the_timeout_and_keeps_the_step_waiting(env):
    o, _ = _orch(env)
    rid = o.submit("g", lambda p: _plan(_step("a", ["kaggle.com"])))
    ticks = iter(range(0, 1000, 10))
    assert o.wait_for_egress(rid, timeout=25, sleep=lambda _: None, clock=lambda: next(ticks)) is False
    assert o.dispatches[rid] == [] and o.egress_pending(rid) == ["kaggle.com"]


def test_wait_for_egress_is_a_noop_when_the_run_asked_for_nothing(env):
    o, _ = _orch(env)
    rid = o.submit("g", lambda p: _plan(_step("a", ["github.com"])))
    assert _pending(env) == []
    assert o.wait_for_egress(rid, sleep=lambda _: pytest.fail("must not wait")) is False


def test_wait_for_egress_returns_false_once_everything_is_dispatched(env):
    o, _ = _orch(env)
    rid = o.submit("g", lambda p: _plan(_step("a", ["kaggle.com"])))
    _approve(env, _pending(env)[0])
    assert o.wait_for_egress(rid, sleep=lambda _: None) is True
    assert o.wait_for_egress(rid, sleep=lambda _: None) is False  # nothing left: the drive loop can end


def test_denied_approval_lets_the_step_fail_with_the_placement_reason(env):
    o, _ = _orch(env)
    rid = o.submit("g", lambda p: _plan(_step("a", ["kaggle.com"])))
    item = _pending(env)[0]
    _queue(env).decide(item["id"], approve=False, by="luke@test", note="no", expect_hash=item["payload_hash"])
    assert o.wait_for_egress(rid, sleep=lambda _: None) is True
    (d,) = o.dispatches[rid]
    assert d["placement"] == "blocked" and "kaggle.com" in d["reason"] and "not approved" in d["reason"]
    assert o.results(rid)["a"]["state"] == "FAILED"


def test_hosts_that_are_not_plain_public_names_are_never_put_to_a_human(env):
    o, _ = _orch(env)
    rid = o.submit("g", lambda p: _plan(_step("a", ["localhost", "169.254.169.254", "*.evil.com"]),
                                        _step("b", ["kaggle.com"])))
    assert _pending(env)[0]["payload"]["hosts"] == ["kaggle.com"]
    assert {d["subtask"]: d["placement"] for d in o.dispatches[rid]} == {"a": "blocked"}  # bad hosts block as before


def test_nothing_is_requested_when_every_host_is_already_approved(env, monkeypatch):
    monkeypatch.setenv("OC_EGRESS_ALLOW", "my-project.example.org")
    o, _ = _orch(env)
    rid = o.submit("g", lambda p: _plan(_step("a", ["my-project.example.org", "api.duckduckgo.com"])))
    assert _pending(env) == []
    assert o.dispatches[rid][0]["placement"] == "cloud-sandbox"
    assert not [r for r in o.audit.rows() if r["event"] == "egress_approval_requested"]


def test_planner_host_case_is_normalised(env):
    o, _ = _orch(env)
    rid = o.submit("g", lambda p: _plan(_step("a", ["GitHub.com "])))
    assert o.dispatches[rid][0]["placement"] == "cloud-sandbox" and _pending(env) == []


def test_queue_failure_does_not_fail_the_run(env, monkeypatch):
    o, egress = _orch(env)
    monkeypatch.setattr(egress, "request", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    rid = o.submit("g", lambda p: _plan(_step("a", ["kaggle.com"])))
    (d,) = o.dispatches[rid]
    assert d["placement"] == "blocked"  # old behaviour: the step blocks with words, the run still renders


def test_recover_rebuilds_the_hold_from_disk(env):
    o, _ = _orch(env)
    rid = o.submit("g", lambda p: _plan(_step("a", ["kaggle.com"])))
    fresh, _ = _orch(env)  # a restarted process only sees disk
    assert fresh.recover(rid) and fresh.egress_pending(rid) == ["kaggle.com"]
    assert fresh.dispatch_ready(rid) == []  # still held after the restart
    _approve(env, _pending(env)[0])
    assert [d["subtask"] for d in fresh.dispatch_ready(rid)] == ["a"]


def test_grant_writes_hosts_to_the_policy_and_only_on_approval(env):
    from services.execplane.policy import PolicyEngine
    from services.opsview.agentgrants import policy_path

    o, _ = _orch(env)
    o.submit("g", lambda p: _plan(_step("a", ["kaggle.com", "datasets.example.org"])))
    item = _pending(env)[0]
    _queue(env).decide(item["id"], approve=False, by="luke@test", note="no", expect_hash=item["payload_hash"])
    assert not policy_path().exists()  # a denial grants nothing
    o2, _ = _orch(env, "run2")
    o2.submit("g", lambda p: _plan(_step("a", ["kaggle.com", "datasets.example.org"])))
    _approve(env, _pending(env)[0])
    assert set(PolicyEngine(policy_path()).approved_egress()) == {"kaggle.com", "datasets.example.org"}


def test_stale_hash_refuses_before_any_grant(env):
    from services.opsview.agentgrants import policy_path

    o, _ = _orch(env)
    o.submit("g", lambda p: _plan(_step("a", ["kaggle.com"])))
    item = _pending(env)[0]
    with pytest.raises(QueueError):
        _queue(env).decide(item["id"], approve=True, by="luke@test", expect_hash="stale", before_approve=grant_run_hosts)
    assert not policy_path().exists() and _queue(env).get(item["id"])["status"] == "pending"


def test_tampered_or_invalid_payload_grants_nothing(env):
    from services.opsview.agentgrants import policy_path

    base = {"kind": "egress", "run": "r", "hosts": ["kaggle.com", "localhost"]}
    from services.gate.queue import payload_hash

    with pytest.raises(GrantError):  # one bad host refuses the whole request
        grant_run_hosts({"payload": base, "payload_hash": payload_hash(base)})
    ok = {"kind": "egress", "run": "r", "hosts": ["kaggle.com"]}
    with pytest.raises(GrantError):  # stored payload no longer matches the hash it was approved under
        grant_run_hosts({"payload": ok, "payload_hash": "0" * 64})
    with pytest.raises(GrantError):  # not an egress request at all
        grant_run_hosts({"payload": {"kind": "other"}, "payload_hash": payload_hash({"kind": "other"})})
    assert not policy_path().exists()
