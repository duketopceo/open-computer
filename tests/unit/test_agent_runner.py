"""JobRunner: submit, lease, run-vs-resume, startup resume. Fakes only; threads are joined, no sleeps beyond a poll."""

from __future__ import annotations

import threading
import time

import pytest

from services.agents.approvals import ApprovalStore, lease
from services.agents.loop import DEFAULT_ORCHESTRATOR_MODEL
from services.agents.replay import replay
from services.agents.runner import JobRunner, join_all
from services.agents.trail import Trail
from services.agents.worker import WorkerResult
from tests.unit.test_agents import StubWorker, _spawn

FIN = {"action": "finish", "summary": "done"}


class Plan:
    def __init__(self, steps):
        self.steps, self.states = list(steps), []

    def __call__(self, state):
        self.states.append(state)
        return {"steps": ["a"]} if state["phase"] == "plan" else self.steps.pop(0)


class Fx:
    """Factory recorder: hands out one Plan/StubWorker per job and remembers the args it was called with."""

    def __init__(self, steps, results=()):
        self.steps, self.results, self.calls, self.plans = steps, results, [], []

    def __call__(self, job, goal, budget, model):
        self.calls.append((job, goal, budget, model))
        plan = Plan(self.steps)
        self.plans.append(plan)
        return {"planner": plan, "worker": StubWorker(list(self.results)), "cwd": "/tmp"}


@pytest.fixture
def parts(tmp_path):
    return Trail(tmp_path / "trail.ndjson"), ApprovalStore(tmp_path / "ap.ndjson"), tmp_path / "leases"


def _runner(parts, factory=None, **kw):
    t, s, d = parts
    return JobRunner(t, s, d, factory, poll_s=0.001, **kw)


def _auto_approve(store, stop):
    def go():
        while not stop.is_set():
            for r in store.pending():
                try:
                    store.decide(r["id"], True, "test", r["hash"])
                except Exception:
                    pass
            time.sleep(0.001)

    th = threading.Thread(target=go, daemon=True)
    th.start()
    return th


def test_submit_without_factory_queues_and_calls_nothing(parts):
    t, s, _ = parts
    job = _runner(parts).submit("do it", None, "luke")
    rows = t.rows(job)
    assert [r["event"] for r in rows] == ["job_submit"]
    r = rows[0]
    assert (r["goal"], r["budget"], r["model"], r["by"]) == ("do it", 25.0, DEFAULT_ORCHESTRATOR_MODEL, "luke")
    assert len(job) == 10 and not s.items()


def test_submit_with_factory_runs_to_finished(parts):
    t, s, _ = parts
    fx = Fx([_spawn("one", 2.0), FIN], [WorkerResult("done", "r", "end_turn", 1.0)])
    stop = threading.Event()
    _auto_approve(s, stop)
    job = _runner(parts, fx).submit("ship it", 10, "luke")
    join_all(5)
    stop.set()
    st = replay(t.rows(job))
    assert st.outcome["status"] == "finished" and st.spent == 1.0
    assert fx.calls == [(job, "ship it", 10.0, DEFAULT_ORCHESTRATOR_MODEL)]
    assert [r["event"] for r in t.rows(job)][:2] == ["job_submit", "job_start"]


def test_budget_default_and_ceiling(parts, monkeypatch):
    r = _runner(parts)
    j = r.submit("g", None, "u")
    assert parts[0].rows(j)[0]["budget"] == 25.0
    for bad in (25.01, 0, -1, "abc", float("nan"), True):
        with pytest.raises(ValueError):
            r.submit("g", bad, "u")
    monkeypatch.setenv("OC_AGENT_MAX_BUDGET", "100")
    assert parts[0].rows(r.submit("g", 60, "u"))[0]["budget"] == 60.0
    monkeypatch.setenv("OC_AGENT_MAX_BUDGET", "junk")
    with pytest.raises(ValueError):
        r.submit("g", 25.5, "u")
    assert len(parts[0].jobs()) == 2  # refused submits wrote nothing


def test_resume_all_drives_unfinished_skips_leased_and_finished(parts):
    t, s, d = parts
    r0 = _runner(parts)
    queued, leased, done = (r0.submit(g, None, "u") for g in ("q", "l", "d"))
    t.append(done, "job_end", status="finished", summary="", spent=0, rounds=0)
    fx = Fx([FIN])
    stop = threading.Event()
    _auto_approve(s, stop)
    with lease(d / f"{leased}.lock") as held:
        assert held
        n = _runner(parts, fx).resume_all()
        join_all(5)
    stop.set()
    assert n == 1 and [c[0] for c in fx.calls] == [queued] and fx.calls[0][1] == "q"
    assert replay(t.rows(queued)).outcome["status"] == "finished"
    assert replay(t.rows(leased)).outcome is None
    assert [x["event"] for x in t.rows(done)] == ["job_submit", "job_end"]


def test_resume_all_continues_started_job_with_resume_not_run(parts):
    t, s, _ = parts
    t.append("aaaaaaaaaa", "job_start", goal="g", budget=9.0, model="m/x")
    t.append("aaaaaaaaaa", "plan", steps=["a"])
    t.append("aaaaaaaaaa", "approval_request", kind="plan", detail={"steps": ["a"]})
    t.append("aaaaaaaaaa", "approval", kind="plan", approved=True)
    fx = Fx([FIN])
    _runner(parts, fx).resume_all()
    join_all(5)
    assert replay(t.rows("aaaaaaaaaa")).outcome["status"] == "finished"
    assert [p["phase"] for p in fx.plans[0].states] == ["step"]  # no new plan
    assert fx.calls == [("aaaaaaaaaa", "g", 9.0, "m/x")]
    assert [r["event"] for r in t.rows("aaaaaaaaaa")].count("job_start") == 1


def test_resume_voids_non_plan_requests_and_reattaches_plan(parts):
    t, s, _ = parts
    j = "bbbbbbbbbb"
    t.append(j, "job_start", goal="g", budget=9.0, model="m/x")
    t.append(j, "plan", steps=["a"])
    t.append(j, "approval_request", kind="plan", detail={"steps": ["a"]})
    plan_id = s.open(j, "plan", {"steps": ["a"]})
    risky_id = s.open(j, "risky", {"action": "push", "title": "t"})
    fx = Fx([FIN])
    _runner(parts, fx).resume_all()
    for _ in range(2000):
        if s.get(risky_id)["status"] == "expired" and fx.calls:
            break
        time.sleep(0.001)
    assert s.get(risky_id)["status"] == "expired"
    pend = s.pending(j)
    assert [p["id"] for p in pend] == [plan_id]  # re-attached, not duplicated
    s.decide(plan_id, True, "luke", pend[0]["hash"])
    join_all(5)
    assert replay(t.rows(j)).outcome["status"] == "finished"
    assert [p["phase"] for p in fx.plans[0].states] == ["step"]  # approving the plan did not ask for a new plan


def test_stale_job_is_not_resumed(parts):
    t, s, _ = parts
    t.append("cccccccccc", "job_start", goal="g", budget=9.0, model="m/x")
    fx = Fx([FIN])
    r = _runner(parts, fx, clock=lambda: time.time() + 86400 + 10)
    assert r.resume_all() == 0
    join_all(1)
    assert fx.calls == [] and len(t.rows("cccccccccc")) == 1
    stop = threading.Event()
    _auto_approve(s, stop)
    assert _runner(parts, fx, clock=lambda: time.time() + 100).resume_all() == 1
    join_all(5)
    stop.set()


def test_resume_age_bound_env(parts, monkeypatch):
    t, _, _ = parts
    monkeypatch.setenv("OC_AGENT_RESUME_MAX_AGE_S", "5")
    t.append("dddddddddd", "job_start", goal="g", budget=9.0, model="m/x")
    fx = Fx([FIN])
    assert _runner(parts, fx, clock=lambda: time.time() + 60).resume_all() == 0
    assert fx.calls == []


def test_resume_all_without_factory_does_nothing(parts):
    t, _, _ = parts
    t.append("eeeeeeeeee", "job_start", goal="g", budget=9.0, model="m/x")
    assert _runner(parts).resume_all() == 0
    assert len(t.rows("eeeeeeeeee")) == 1


def test_setup_failure_ends_the_job_failed_with_a_redacted_reason(parts):
    t, s, d = parts

    def bad_factory(job, goal, budget, model):
        raise RuntimeError("no key sk-ant-api03-" + "A" * 30)

    job = _runner(parts, bad_factory).submit("x", None, "luke")
    join_all(5)
    st = replay(t.rows(job))
    assert st.outcome is not None and st.outcome["status"] == "failed"
    assert "RuntimeError" in st.outcome["summary"] and "A" * 30 not in st.outcome["summary"]
    with lease(d / f"{job}.lock") as free:  # the lease is released
        assert free
    assert _runner(parts, bad_factory).resume_all() == 0  # a failed job is finished, not retried


def test_setup_failure_on_a_resumed_job_also_ends_it(parts):
    t, s, d = parts
    ok = Fx([FIN])
    job = _runner(parts).submit("x", None, "luke")  # queued, no factory
    t.append(job, "job_start", goal="x", budget=25.0, model="m")  # started, never finished

    def bad_factory(job, goal, budget, model):
        raise ValueError("config missing")

    assert _runner(parts, bad_factory).resume_all() == 1
    join_all(5)
    assert replay(t.rows(job)).outcome["status"] == "failed"
    assert ok.calls == []
