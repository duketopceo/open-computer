"""Trail replay and AgentLoop.resume: a job continues from its trail with correct spend and no repeated rounds.
Fakes only (scripted planner, stub worker)."""

from __future__ import annotations

import json

import pytest

from services.agents.loop import AgentLoop
from services.agents.replay import replay
from services.agents.trail import Trail
from services.agents.worker import WorkerResult
from services.cost import CostLedger
from tests.unit.test_agents import StubWorker, _spawn

FIN = {"action": "finish", "summary": "done"}


class Plan:
    """Scripted planner that records every state it is asked about."""

    def __init__(self, steps):
        self.steps, self.states = list(steps), []

    def __call__(self, state):
        self.states.append(state)
        return {"steps": ["a"]} if state["phase"] == "plan" else self.steps.pop(0)

    def asked(self, phase):
        return [s for s in self.states if s["phase"] == phase]


def _ok(cost=1.0):
    return WorkerResult("done", "r", "end_turn", cost)


def _mk(tmp_path, planner, worker, **kw):
    kw.setdefault("approve", lambda k, d: True)
    return AgentLoop(Trail(tmp_path / "trail.ndjson"), planner, worker, model="cheap/a", cwd=str(tmp_path), **kw)


def _truncate(tmp_path, n, *, raw_tail=""):
    """Keep the first n rows of trail.ndjson: the process died right after row n."""
    p = tmp_path / "trail.ndjson"
    lines = p.read_text().splitlines()[:n]
    p.write_text("".join(ln + "\n" for ln in lines) + raw_tail)


def _row_index(tmp_path, event, nth=1, **match):
    seen = 0
    for i, r in enumerate(Trail(tmp_path / "trail.ndjson").rows(), 1):
        if r["event"] == event and all(r.get(k) == v for k, v in match.items()):
            seen += 1
            if seen == nth:
                return i
    raise AssertionError(event)


def _shape(rows):
    return [{k: v for k, v in r.items() if k not in ("ts", "seq")} for r in rows]


def _steps_three():
    return [_spawn("one", 2.0), _spawn("two", 2.0), _spawn("three", 2.0), FIN]


def _full(tmp_path, job="j"):
    wk = StubWorker([_ok(1.5), _ok(0.5), _ok(2.0)])
    out = _mk(tmp_path, Plan(_steps_three()), wk).run("the goal", job=job)
    return out


# ------------------------------------------------------------ replay equivalence
def test_resumed_job_equals_uninterrupted_run(tmp_path):
    full_dir = tmp_path / "full"
    full_dir.mkdir()
    want = _full(full_dir)
    want_rows = _shape(Trail(full_dir / "trail.ndjson").rows())

    cut_dir = tmp_path / "cut"
    cut_dir.mkdir()
    _full(cut_dir)
    _truncate(cut_dir, _row_index(cut_dir, "result", 2))  # died after round 2's result
    plan = Plan([_spawn("three", 2.0), FIN])
    wk = StubWorker([_ok(2.0)])
    got = _mk(cut_dir, plan, wk).resume("j")

    assert got.status == want.status == "finished" and got.spent == want.spent == 4.0 and got.rounds == want.rounds
    assert len(wk.tasks) == 1 and wk.tasks[0].prompt == "do three"  # rounds 1-2 not re-run
    assert plan.asked("plan") == []
    assert _shape(Trail(cut_dir / "trail.ndjson").rows()) == want_rows
    step = plan.asked("step")[0]
    assert step["round"] == 3 and step["spent"] == 2.0 and [h["round"] for h in step["history"]] == [1, 2]


def test_replay_state_from_a_full_run(tmp_path):
    _full(tmp_path)
    st = replay(Trail(tmp_path / "trail.ndjson").rows("j"))
    assert st.outcome["status"] == "finished" and st.spent == 4.0 and st.last_round == 3 and st.inflight is None
    assert st.plan_approved is True and st.goal == "the goal" and st.budget == 25.0 and st.model == "cheap/a"
    assert [h["round"] for h in st.history] == [1, 2, 3]


# ------------------------------------------------------------ AE1 and interrupted rounds
def test_ae1_interrupted_round_is_charged_its_estimate_once(tmp_path):
    led = CostLedger(tmp_path / "cost.ndjson")
    _full(tmp_path)
    _truncate(tmp_path, _row_index(tmp_path, "spawn", 3))  # rounds 1-2 done, round 3 spawned, no result
    plan = Plan([_spawn("four", 1.0), FIN])
    wk = StubWorker([_ok(1.0)])
    out = _mk(tmp_path, plan, wk, ledger=led).resume("j")

    rows = Trail(tmp_path / "trail.ndjson").rows("j")
    assert len(wk.tasks) == 1 and wk.tasks[0].prompt == "do four"  # not round 1-2, not a replay of round 3
    ri = [r for r in rows if r["event"] == "round_interrupted"]
    assert len(ri) == 1 and ri[0]["round"] == 3 and ri[0]["charged"] == 2.0
    assert plan.asked("step")[0]["round"] == 4
    assert plan.asked("step")[0]["spent"] == 4.0  # 1.5 + 0.5 + 2.0 estimate
    assert out.status == "finished" and out.spent == 5.0
    assert led.by_subtask("j")["round-3"] == 2.0
    h = plan.asked("step")[0]["history"]
    assert h[-1]["round"] == 3 and h[-1]["status"] == "failed"


def test_resume_twice_charges_once(tmp_path):
    led = CostLedger(tmp_path / "cost.ndjson")
    _full(tmp_path)
    _truncate(tmp_path, _row_index(tmp_path, "spawn", 3))

    def die(state):
        raise RuntimeError("die")

    _mk(tmp_path, die, StubWorker([]), ledger=led).resume("j")  # dies after the interrupted row: job_end "failed"
    p = tmp_path / "trail.ndjson"
    p.write_text("".join(ln + "\n" for ln in p.read_text().splitlines() if json.loads(ln)["event"] != "job_end"))
    _mk(tmp_path, Plan([FIN]), StubWorker([]), ledger=led).resume("j")  # second process: same crash state
    events = [r["event"] for r in Trail(p).rows("j")]
    assert events.count("round_interrupted") == 1
    assert led.by_subtask("j")["round-3"] == 2.0


def test_finished_job_returns_recorded_outcome_and_appends_nothing(tmp_path):
    want = _full(tmp_path)
    n = len(Trail(tmp_path / "trail.ndjson").rows())
    plan, wk = Plan([]), StubWorker([])
    got = _mk(tmp_path, plan, wk).resume("j")
    assert (got.status, got.spent, got.rounds) == (want.status, want.spent, want.rounds)
    assert len(Trail(tmp_path / "trail.ndjson").rows()) == n and plan.states == [] and wk.tasks == []


def test_decision_without_spawn_is_replanned_and_free(tmp_path):
    _full(tmp_path)
    _truncate(tmp_path, _row_index(tmp_path, "decision", 3))
    plan = Plan([_spawn("three", 2.0), FIN])
    out = _mk(tmp_path, plan, StubWorker([_ok(2.0)])).resume("j")
    rows = Trail(tmp_path / "trail.ndjson").rows("j")
    assert not [r for r in rows if r["event"] == "round_interrupted"]
    assert plan.asked("step")[0]["round"] == 3 and out.spent == 4.0


def test_plan_row_without_resolved_approval_reuses_the_plan(tmp_path):
    _full(tmp_path)
    _truncate(tmp_path, _row_index(tmp_path, "plan"))
    plan, asked = Plan([FIN]), []
    out = _mk(tmp_path, plan, StubWorker([]), approve=lambda k, d: asked.append((k, d)) or True).resume("j")
    assert plan.asked("plan") == [] and asked == [("plan", {"steps": ["a"]})]
    assert out.status == "finished"


# ------------------------------------------------------------ warned / granted
def _ask_log():
    asked = []

    def approve(kind, detail):
        asked.append(kind)
        return True

    return approve, asked


def test_warned_and_granted_restored_from_approved_rows(tmp_path):
    steps = [_spawn("a", 8.5, heavy=True, egress=["api.example.com"]), _spawn("b", 1.0, heavy=True,
             egress=["api.example.com"]), FIN]
    _mk(tmp_path, Plan(steps), StubWorker([_ok(8.5), _ok(1.0)]), budget=10.0).run("g", job="j")
    _truncate(tmp_path, _row_index(tmp_path, "result", 1))
    approve, asked = _ask_log()
    st = replay(Trail(tmp_path / "trail.ndjson").rows("j"))
    assert st.warned is True and st.granted == {"api.example.com"}
    plan = Plan([_spawn("b", 1.0, heavy=True, egress=["api.example.com"]), FIN])
    _mk(tmp_path, plan, StubWorker([_ok(1.0)]), budget=10.0, approve=approve).resume("j")
    assert asked == []  # no second 80% ask, no second egress ask


@pytest.mark.parametrize("pending", ["spend", "egress"])
def test_pending_request_is_asked_again_and_does_not_spawn_until_decided(tmp_path, pending):
    first = _spawn("a", 9.0, heavy=True, egress=["api.example.com"])
    _mk(tmp_path, Plan([first, FIN]), StubWorker([_ok(9.0)]), budget=10.0).run("g", job="j")
    _truncate(tmp_path, _row_index(tmp_path, "approval_request", kind=pending))  # asked, owner has not answered
    st = replay(Trail(tmp_path / "trail.ndjson").rows("j"))
    assert st.warned is (pending == "egress") and st.granted == set()
    asked, wk = [], StubWorker([_ok(9.0)])

    def approve(kind, detail):
        asked.append(kind)
        return kind == "plan"  # the owner has not decided the pending request: nothing may spawn

    out = _mk(tmp_path, Plan([first, FIN]), wk, budget=10.0, approve=approve).resume("j")
    assert pending in asked and wk.tasks == [] and out.status in ("budget_exhausted", "stalled")


# ------------------------------------------------------------ goal, est, stall, budget, torn
def test_long_goal_from_job_submit_reaches_the_resumed_planner(tmp_path):
    goal = "g" * 900
    t = Trail(tmp_path / "trail.ndjson")
    t.append("j", "job_submit", goal=goal, budget=7.0, model="cheap/z")
    t.append("j", "job_start", goal=goal[:500], budget=25.0, model="cheap/a")
    plan = Plan([FIN])
    out = _mk(tmp_path, plan, StubWorker([])).resume("j")
    assert plan.asked("plan")[0]["goal"] == goal and plan.asked("plan")[0]["budget"] == 7.0
    assert out.status == "finished"


def test_goal_falls_back_to_job_start(tmp_path):
    t = Trail(tmp_path / "trail.ndjson")
    t.append("j", "job_start", goal="short", budget=3.0, model="cheap/a")
    st = replay(t.rows("j"))
    assert (st.goal, st.budget, st.model) == ("short", 3.0, "cheap/a")


def test_spawn_row_without_est_charges_the_default_when_interrupted(tmp_path):
    t = Trail(tmp_path / "trail.ndjson")
    t.append("j", "job_start", goal="g", budget=25.0, model="cheap/a")
    t.append("j", "plan", steps=["a"])
    t.append("j", "approval", kind="plan", approved=True)
    t.append("j", "decision", round=1, action="spawn", title="t")
    t.append("j", "spawn", round=1, title="t", placement="local", worker="stub")  # older row: no est
    out = _mk(tmp_path, Plan([FIN]), StubWorker([])).resume("j")
    ri = [r for r in t.rows("j") if r["event"] == "round_interrupted"]
    assert ri[0]["charged"] == 1.0 and out.spent == 1.0


def test_spawn_row_carries_est(tmp_path):
    _full(tmp_path)
    assert [r["est"] for r in Trail(tmp_path / "trail.ndjson").rows("j") if r["event"] == "spawn"] == [2.0] * 3


def test_failure_count_survives_resume(tmp_path):
    bad = WorkerResult("failed", "", "error")
    _mk(tmp_path, Plan([_spawn()] * 3), StubWorker([bad, bad, bad]), max_failures=3).run("g", job="j")
    _truncate(tmp_path, _row_index(tmp_path, "result", 2))
    wk = StubWorker([bad])
    out = _mk(tmp_path, Plan([_spawn()]), wk, max_failures=3).resume("j")
    assert out.status == "stalled" and out.rounds == 3 and len(wk.tasks) == 1


def test_resumed_budget_check_uses_recomputed_spend(tmp_path):
    _mk(tmp_path, Plan([_spawn(est=4.0), _spawn(est=4.0), FIN]), StubWorker([_ok(4.0), _ok(4.0)]),
        budget=10.0).run("g", job="j")
    _truncate(tmp_path, _row_index(tmp_path, "result", 2))
    wk = StubWorker([])
    out = _mk(tmp_path, Plan([_spawn(est=4.0)]), wk, budget=10.0).resume("j")
    assert out.status == "budget_exhausted" and out.spent == 8.0 and wk.tasks == []


def test_torn_last_line_does_not_break_replay(tmp_path):
    _full(tmp_path)
    _truncate(tmp_path, _row_index(tmp_path, "result", 2), raw_tail='{"job": "j", "seq": 99, "ev')
    out = _mk(tmp_path, Plan([_spawn("three", 2.0), FIN]), StubWorker([_ok(2.0)])).resume("j")
    assert out.status == "finished" and out.spent == 4.0


# ------------------------------------------------------------ R9 sandbox charge
@pytest.mark.parametrize("placement,reported,want", [
    ({"heavy": True}, 0.0, 3.0), ({"heavy": True}, 5.0, 5.0), ({"heavy": True}, None, 3.0),
    ({}, 0.0, 0.0), ({}, 5.0, 5.0)])
def test_sandbox_rounds_charge_at_least_the_estimate(tmp_path, placement, reported, want):
    led = CostLedger(tmp_path / "cost.ndjson")
    out = _mk(tmp_path, Plan([_spawn("t", 3.0, **placement), FIN]),
              StubWorker([WorkerResult("done", "r", "end_turn", reported)]), ledger=led).run("g", job="j")
    assert out.spent == want and led.total("j") == pytest.approx(want)
