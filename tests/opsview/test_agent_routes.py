"""Console routes for agent jobs: submit, approve, deny, and the egress grant. Fakes only: no model, no VM, no
network; OC_EXECPLANE_DIR is a tmp dir so no real policy is touched."""

from __future__ import annotations

import json
import threading
import time

import pytest

from services.agents.approvals import ApprovalStore
from services.agents.replay import replay
from services.agents.runner import join_all
from services.agents.trail import Trail
from services.execplane.policy import PolicyEngine
from services.opsview import agentjobs, server
from tests.unit.test_agents import StubWorker, _spawn
from tests.unit.test_agent_runner import Plan

J = "0123456789"
JSON = {"Content-Type": "application/json", "Accept": "application/json"}


@pytest.fixture(autouse=True)
def agent_env(tmp_path, monkeypatch):
    monkeypatch.setenv("OC_EXECPLANE_DIR", str(tmp_path / "exec"))
    monkeypatch.setattr(agentjobs, "_factory", None)
    monkeypatch.setattr(agentjobs, "POLL_S", 0.005)
    yield
    join_all(1)


def post(console, path, obj, *, csrf=True, headers=None):
    h = {**JSON, **(headers or {})}
    if csrf:
        h["X-CSRF-Token"] = console.csrf()
    s, _, b = console.request("POST", path, body=json.dumps(obj), headers=h)
    return s, json.loads(b)


def get(console, path):
    s, _, b = console.get(path, headers={"Accept": "application/json"})
    return s, json.loads(b)


def store():
    return ApprovalStore(agentjobs.approvals_path())


def policy(tmp_path):
    return PolicyEngine(tmp_path / "exec" / "policy.json")


def ask(kind="egress", detail=None, job=J):
    detail = detail if detail is not None else {"host": "api.example.com", "title": "t", "placement": "cloud-sandbox"}
    rid = store().open(job, kind, detail)
    return rid, store().get(rid)["hash"]


def ap(job, rid, verb):
    return f"/api/agent-jobs/{job}/approvals/{rid}/{verb}"


# ---------------------------------------------------------------- submit
def test_submit_needs_confirm_and_records_nothing(console):
    s, b = post(console, "/api/agent-jobs", {"goal": "ship the thing"})
    assert (s, b) == (200, {"needs_confirm": True, "budget": 25.0, "model": "deepseek/deepseek-v4-pro"})
    assert get(console, "/api/agent-jobs") == (200, {"jobs": []})
    s, b = post(console, "/api/agent-jobs", {"goal": "x", "budget": 5})
    assert b["budget"] == 5.0 and get(console, "/api/agent-jobs")[1] == {"jobs": []}


def test_submit_confirm_queues_when_no_runner(console):
    s, b = post(console, "/api/agent-jobs", {"goal": "ship the thing", "confirm": "1"})
    assert s == 200 and list(b) == ["job"] and len(b["job"]) == 10
    jobs = get(console, "/api/agent-jobs")[1]["jobs"]
    assert len(jobs) == 1
    j = jobs[0]
    assert (j["job"], j["goal"], j["status"], j["budget"], j["spent"]) == (b["job"], "ship the thing", "queued", 25.0, 0)
    assert j["awaiting_approval"] is False and j["pending"] == 0 and j["started"]
    rows = Trail(agentjobs.trail_path()).rows(b["job"])
    assert [r["event"] for r in rows] == ["job_submit"] and rows[0]["by"] == "luke@test"


@pytest.mark.parametrize("body", [{"goal": ""}, {"goal": "   "}, {"goal": "x" * 4001}, {}])
def test_submit_bad_goal_is_422(console, body):
    s, b = post(console, "/api/agent-jobs", {**body, "confirm": "1"})
    assert s == 422 and "error" in b
    assert get(console, "/api/agent-jobs")[1] == {"jobs": []}


@pytest.mark.parametrize("budget", [25.01, 0, -3, "abc", True])
def test_submit_bad_budget_is_422_and_writes_nothing(console, budget):
    for confirm in ("", "1"):
        s, b = post(console, "/api/agent-jobs", {"goal": "g", "budget": budget, "confirm": confirm})
        assert s == 422 and "error" in b
    assert get(console, "/api/agent-jobs")[1] == {"jobs": []}


def test_submit_budget_max_comes_from_env(console, monkeypatch):
    monkeypatch.setenv("OC_AGENT_MAX_BUDGET", "100")
    s, b = post(console, "/api/agent-jobs", {"goal": "g", "budget": 80, "confirm": "1"})
    assert s == 200 and get(console, "/api/agent-jobs")[1]["jobs"][0]["budget"] == 80.0


def test_new_routes_refuse_without_csrf_and_on_oversize(console):
    for path in ("/api/agent-jobs", ap(J, "ag_00000000", "approve"), ap(J, "ag_00000000", "deny")):
        assert post(console, path, {"goal": "g", "confirm": "1"}, csrf=False)[0] == 403
        s, _, _ = console.request("POST", path, body="x" * 1_100_000,
                                  headers={**JSON, "X-CSRF-Token": console.csrf()})
        assert s == 413


# ---------------------------------------------------------------- approve / deny (AE2)
def test_egress_approve_writes_grant_and_decision(console, tmp_path):
    rid, h = ask()
    s, b = post(console, ap(J, rid, "approve"), {"hash": h, "note": "ok"})
    assert (s, b) == (200, {"ok": True})
    assert "api.example.com" in policy(tmp_path).approved_egress()
    r = store().get(rid)
    assert r["status"] == "approved" and r["by"] == "luke@test" and r["note"] == "ok"


def test_stale_hash_is_409_and_nothing_changes(console, tmp_path):
    rid, h = ask()
    s, b = post(console, ap(J, rid, "approve"), {"hash": "0" * 64})
    assert s == 409 and "stale" in b["error"]
    assert store().get(rid)["status"] == "pending"
    assert not (tmp_path / "exec" / "policy.json").exists()


def test_missing_hash_is_422(console, tmp_path):
    rid, _ = ask()
    for verb in ("approve", "deny"):
        assert post(console, ap(J, rid, verb), {})[0] == 422
    assert store().get(rid)["status"] == "pending" and not (tmp_path / "exec" / "policy.json").exists()


def test_replayed_approve_is_409_and_one_grant(console, tmp_path):
    rid, h = ask()
    assert post(console, ap(J, rid, "approve"), {"hash": h})[0] == 200
    assert post(console, ap(J, rid, "approve"), {"hash": h})[0] == 409
    assert len(json.loads((tmp_path / "exec" / "policy.json").read_text())) == 1


def test_denied_then_approve_never_grants(console, tmp_path):
    rid, h = ask()
    assert post(console, ap(J, rid, "deny"), {"hash": h})[1] == {"ok": True}
    assert store().get(rid)["status"] == "denied"
    assert post(console, ap(J, rid, "approve"), {"hash": h})[0] == 409
    assert not (tmp_path / "exec" / "policy.json").exists()


def test_expired_approve_never_grants(console, tmp_path):
    rid = store().open(J, "egress", {"host": "api.example.com"}, ttl_s=0.0)
    h = store().get(rid)["hash"]
    assert post(console, ap(J, rid, "approve"), {"hash": h})[0] == 409
    assert not (tmp_path / "exec" / "policy.json").exists()


@pytest.mark.parametrize("host", ["127.0.0.1", "*.example.com", "localhost", "10.0.0.1", "a.b/c"])
def test_invalid_host_is_refused_without_grant_or_decision(console, tmp_path, host):
    rid, h = ask(detail={"host": host})
    s, b = post(console, ap(J, rid, "approve"), {"hash": h})
    assert s == 422 and "error" in b
    assert store().get(rid)["status"] == "pending"
    assert not (tmp_path / "exec" / "policy.json").exists()


def test_grant_failure_is_500_and_request_stays_pending(console, tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv("OC_EXECPLANE_DIR", str(blocker / "sub"))  # unwritable: parent is a file
    rid, h = ask()
    s, b = post(console, ap(J, rid, "approve"), {"hash": h})
    assert s == 500 and "error" in b
    assert store().get(rid)["status"] == "pending"


def test_already_granted_host_is_not_granted_twice(console, tmp_path):
    for _ in range(2):
        rid = store().open(J, "egress", {"host": "api.example.com", "n": _})
        assert post(console, ap(J, rid, "approve"), {"hash": store().get(rid)["hash"]})[0] == 200
    entries = json.loads((tmp_path / "exec" / "policy.json").read_text())
    assert len(entries) == 1


def test_concurrent_approves_of_one_request_write_one_grant(console, tmp_path):
    rid, h = ask()
    out, tokens = [], [console.csrf() for _ in range(6)]

    def go(t):
        s, _, _ = console.request("POST", ap(J, rid, "approve"), body=json.dumps({"hash": h}),
                                  headers={**JSON, "X-CSRF-Token": t})
        out.append(s)

    ts = [threading.Thread(target=go, args=(t,)) for t in tokens]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(out) == [200] + [409] * 5
    assert len(json.loads((tmp_path / "exec" / "policy.json").read_text())) == 1


def test_concurrent_approvals_of_different_hosts_both_granted(console, tmp_path):
    hosts = [f"h{i}.example.com" for i in range(6)]
    reqs = [ask(detail={"host": h}) for h in hosts]
    out, token = [], console.csrf()

    def go(rid, h):
        s, _, _ = console.request("POST", ap(J, rid, "approve"), body=json.dumps({"hash": h}),
                                  headers={**JSON, "X-CSRF-Token": token})
        out.append(s)

    ts = [threading.Thread(target=go, args=r) for r in reqs]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert out == [200] * 6
    assert set(policy(tmp_path).approved_egress()) == set(hosts)


def test_non_egress_approve_needs_no_grant(console, tmp_path):
    rid, h = ask("risky", {"action": "push", "title": "t"})
    assert post(console, ap(J, rid, "approve"), {"hash": h})[0] == 200
    assert store().get(rid)["status"] == "approved" and not (tmp_path / "exec" / "policy.json").exists()


def test_unknown_or_foreign_approval_is_404(console):
    assert post(console, ap(J, "ag_deadbeef", "approve"), {"hash": "x"})[0] == 404
    rid, h = ask(job="aaaaaaaaaa")
    assert post(console, ap(J, rid, "approve"), {"hash": h})[0] == 404  # id belongs to another job
    assert store().get(rid)["status"] == "pending"
    s, _, _ = console.request("POST", "/api/agent-jobs/short/approvals/ag_deadbeef/approve", body="{}",
                              headers={**JSON, "X-CSRF-Token": console.csrf()})
    assert s == 404


# ---------------------------------------------------------------- GET detail
def test_get_detail_lists_pending_approvals(console):
    Trail(agentjobs.trail_path()).append(J, "job_start", goal="the goal", budget=9.0, model="m/x")
    rid, h = ask("plan", {"steps": ["a"]})
    s, b = get(console, f"/api/agent-jobs/{J}")
    assert s == 200 and (b["job"], b["goal"], b["status"], b["budget"], b["spent"]) == (J, "the goal", "running", 9.0, 0)
    assert [e["event"] for e in b["events"]] == ["job_start"]
    (a,) = b["approvals"]
    assert (a["id"], a["kind"], a["detail"], a["hash"]) == (rid, "plan", {"steps": ["a"]}, h)
    assert isinstance(a["ts"], float) and a["expires_at"] > a["ts"]
    j = get(console, "/api/agent-jobs")[1]["jobs"][0]
    assert j["awaiting_approval"] is True and j["pending"] == 1 and j["status"] == "running"
    post(console, ap(J, rid, "deny"), {"hash": h})
    assert get(console, f"/api/agent-jobs/{J}")[1]["approvals"] == []
    assert get(console, "/api/agent-jobs")[1]["jobs"][0]["awaiting_approval"] is False


def test_get_detail_unknown_job_is_404(console):
    s, b = get(console, "/api/agent-jobs/ffffffffff")
    assert s == 404 and "error" in b


# ---------------------------------------------------------------- end to end with a fake runner
def _factory(planner, worker):
    return lambda job, goal, budget, model: {"planner": planner, "worker": worker, "cwd": "/tmp"}


def _wait_pending(console, job, kind):
    for _ in range(2000):
        a = [x for x in get(console, f"/api/agent-jobs/{job}")[1]["approvals"] if x["kind"] == kind]
        if a:
            return a[0]
        time.sleep(0.002)
    raise AssertionError(f"no pending {kind}")


def test_submit_runs_job_and_egress_approval_unblocks_it(console, tmp_path):
    from services.agents.worker import WorkerResult

    plan = Plan([_spawn("one", 2.0, heavy=True, egress=["api.example.com"]), {"action": "finish", "summary": "ok"}])
    wk = StubWorker([WorkerResult("done", "r", "end_turn", 1.0)])
    agentjobs.configure_runner(_factory(plan, wk))
    job = post(console, "/api/agent-jobs", {"goal": "g", "confirm": "1"})[1]["job"]
    for kind in ("plan", "egress"):
        a = _wait_pending(console, job, kind)
        assert post(console, ap(job, a["id"], "approve"), {"hash": a["hash"]})[0] == 200
    join_all(5)
    assert "api.example.com" in policy(tmp_path).approved_egress()
    assert replay(Trail(agentjobs.trail_path()).rows(job)).outcome["status"] == "finished"
    j = get(console, "/api/agent-jobs")[1]["jobs"][0]
    assert (j["status"], j["spent"], j["awaiting_approval"]) == ("finished", 2.0, False)  # sandbox round: max(reported, estimate)


def test_denied_plan_ends_rejected(console):
    plan = Plan([])
    agentjobs.configure_runner(_factory(plan, StubWorker([])))
    job = post(console, "/api/agent-jobs", {"goal": "g", "confirm": "1"})[1]["job"]
    a = _wait_pending(console, job, "plan")
    assert post(console, ap(job, a["id"], "deny"), {"hash": a["hash"]})[0] == 200
    join_all(5)
    assert get(console, "/api/agent-jobs")[1]["jobs"][0]["status"] == "rejected"


def test_restart_reattaches_plan_approval_ae3(console):
    t = Trail(agentjobs.trail_path())
    t.append(J, "job_start", goal="g", budget=9.0, model="m/x")
    t.append(J, "plan", steps=["a"])
    t.append(J, "approval_request", kind="plan", detail={"steps": ["a"]})
    store().open(J, "plan", {"steps": ["a"]})
    plan = Plan([{"action": "finish", "summary": "ok"}])
    agentjobs.configure_runner(_factory(plan, StubWorker([])))
    assert agentjobs.resume_all() == 1
    a = _wait_pending(console, J, "plan")
    assert len(get(console, f"/api/agent-jobs/{J}")[1]["approvals"]) == 1
    assert post(console, ap(J, a["id"], "approve"), {"hash": a["hash"]})[0] == 200
    join_all(5)
    assert replay(t.rows(J)).outcome["status"] == "finished"
    assert [s["phase"] for s in plan.states] == ["step"]  # no new plan was requested


def test_startup_resume_fails_soft(monkeypatch):
    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(agentjobs, "resume_all", boom)
    server._resume_agent_jobs()  # must not raise
