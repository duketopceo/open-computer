"""Plan review: a held plan runs nothing until approved; edits are validated; a restart never starts it.

No model, sandbox or network: planner and worker are plain functions."""

import json

import pytest

from services.opsview import goalruns, server

PLAN = [
    {"id": "a", "title": "first", "prompt": "p1", "est_cost": 0.04},
    {"id": "b", "title": "second", "prompt": "p2", "deps": ["a"], "est_cost": 0.04},
]


def _planner(_prompt):
    return json.dumps(PLAN)


def _worker(task):
    return "out:" + task["prompt"][:2]


@pytest.fixture
def goals(tmp_path, monkeypatch):
    monkeypatch.setenv("OC_GOALS_DIR", str(tmp_path))
    return tmp_path


def _held(**kw):
    return goalruns.submit("Do two things", _planner, _worker, by="t", hold=lambda subs: True, **kw)


def _drive_sync(monkeypatch):
    """Run the driver inline so a test can read the finished run."""
    monkeypatch.setattr(goalruns, "_start_drive", lambda orch, rid, wfn: goalruns._drive(orch, rid, wfn, sleep=lambda s: None))


def test_held_plan_dispatches_nothing_and_shows_the_plan(goals):
    rid = _held()
    run = goalruns.get_run(rid)
    assert run["plan"]["state"] == "planned" and run["subtasks"] == []
    assert [s["id"] for s in run["plan"]["steps"]] == ["a", "b"]
    assert run["plan"]["estimate"] == pytest.approx(0.08)
    item = next(r for r in goalruns.list_runs() if r["run"] == rid)
    assert item["plan_state"] == "planned" and item["finished"] is False and item["states"]["QUEUED"] == 0
    assert goalruns.clear_finished() == 0  # a plan waiting for review is never swept away


def test_approve_with_edit_runs_the_edited_plan(goals, monkeypatch):
    _drive_sync(monkeypatch)
    rid = _held()
    code, body = goalruns.decide_plan(rid, "approve", [{"id": "b", "title": "renamed", "prompt": "zz"}], lambda m: _worker)
    assert code == 200 and body["state"] == "approved"
    run = goalruns.get_run(rid)
    assert run["plan"]["state"] == "approved"
    assert [s["title"] for s in run["subtasks"]] == ["first", "renamed"]
    assert all(s["state"] == "DONE" for s in run["subtasks"])
    assert goalruns.decide_plan(rid, "approve", None, lambda m: _worker)[0] == 409  # second click refused


def test_remove_step_prunes_dependents(goals, monkeypatch):
    _drive_sync(monkeypatch)
    rid = _held()
    code, _ = goalruns.decide_plan(rid, "approve", [{"id": "a", "remove": True}], lambda m: _worker)
    run = goalruns.get_run(rid)
    assert code == 200 and run["declared"] == 1 and [s["subtask"] for s in run["subtasks"]] == ["b"]


def test_cancel_spends_nothing_and_stays_cancelled(goals):
    rid = _held()
    assert goalruns.decide_plan(rid, "cancel", None, lambda m: _worker) == (200, {"state": "cancelled"})
    run = goalruns.get_run(rid)
    assert run["plan"]["state"] == "cancelled" and run["subtasks"] == []
    assert next(r for r in goalruns.list_runs() if r["run"] == rid)["finished"] is True
    assert goalruns.decide_plan(rid, "approve", None, lambda m: _worker)[0] == 409


@pytest.mark.parametrize(
    "edits",
    [
        [{"id": "zz", "title": "x"}],
        [{"id": "a", "title": "  "}],
        [{"id": "a", "tier": "premium"}],
        [{"id": "a", "deps": ["b"]}],  # a -> b -> a is a cycle
        [{"id": "a", "remove": True}, {"id": "b", "remove": True}],
        "nope",
    ],
)
def test_invalid_edits_are_refused_and_the_plan_stays_held(goals, edits):
    rid = _held()
    code, body = goalruns.decide_plan(rid, "approve", edits, lambda m: _worker)
    assert code == 422 and body["error"]
    assert goalruns.get_run(rid)["plan"]["state"] == "planned"
    assert [s["id"] for s in goalruns.get_run(rid)["plan"]["steps"]] == ["a", "b"]


def test_cycle_check_is_the_validator(goals, monkeypatch):
    """Negative control: with the topology check off the cyclic edit would be accepted."""
    import importlib

    decompose = importlib.import_module("services.orchestrator.decompose")

    monkeypatch.setattr(decompose, "_topo", lambda tasks: None)
    assert goalruns.apply_edits(list(PLAN), [{"id": "a", "deps": ["b"]}])[0]["deps"] == ["b"]


def test_restart_does_not_start_a_held_plan(goals):
    rid = _held()
    started = []
    assert goalruns.resume(lambda t: started.append(t) or "x") == 0 and started == []
    assert goalruns.get_run(rid)["plan"]["state"] == "planned"


def test_unheld_run_is_unchanged(goals, monkeypatch):
    _drive_sync(monkeypatch)
    rid = goalruns.submit("Do two things", _planner, _worker, by="t")
    assert "plan" not in goalruns.get_run(rid)


def test_hold_policy(monkeypatch):
    small = [{"est_cost": 0.01}]
    big = [{"est_cost": 0.5}]
    many = [{"est_cost": 0}] * 5
    monkeypatch.delenv("OC_PLAN_REVIEW", raising=False)
    h = server.plan_hold()
    assert (h(small), h(big), h(many)) == (False, True, True)
    assert server.plan_hold(False) is None and server.plan_hold(True)(small) is True
    monkeypatch.setenv("OC_PLAN_REVIEW", "off")
    assert server.plan_hold() is None
    monkeypatch.setenv("OC_PLAN_REVIEW", "always")
    assert server.plan_hold()(small) is True
    monkeypatch.setenv("OC_PLAN_REVIEW", "1.0")
    assert server.plan_hold()(big) is False


def test_plan_endpoint(console, goals, monkeypatch):
    rid = _held()
    status, _, raw = console.post(f"/api/runs/{rid}/plan", {"action": "cancel"})
    assert status == 200 and json.loads(raw)["state"] == "cancelled"
    assert console.post(f"/api/runs/{rid}/plan", {"action": "approve"})[0] == 409
