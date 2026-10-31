"""Effort picker backend: GET /api/effort, POST /goals with an effort, and the per-run budget it sets."""

from __future__ import annotations

import json
import time

import pytest

from services.cost import Usage
from services.opsview import effort, goalruns, server
from services.orchestrator.runner import WorkResult

TOKEN_HDR = "X-CSRF-Token"


def post_goal(console, data):
    return console.request(
        "POST",
        "/goals",
        body=json.dumps(data),
        headers={"Content-Type": "application/json", "Accept": "application/json", TOKEN_HDR: console.csrf()},
    )


@pytest.fixture
def plans(monkeypatch):
    rec = []

    def fake(goal, who, plan):
        rec.append(plan)
        return "goal_ab12cd34"

    monkeypatch.setattr(server, "_submit_goal", fake)
    return rec


def test_effort_catalog_is_json_and_matches_the_module(console, monkeypatch):
    monkeypatch.delenv("OC_EFFORT_DEFAULT", raising=False)
    status, headers, body = console.get("/api/effort")
    assert status == 200 and headers["Content-Type"].startswith("application/json")
    d = json.loads(body)
    assert d["default"] == "low" and d["models"] == effort.models()
    assert [lv["id"] for lv in d["levels"]] == ["low", "medium", "high"]
    assert d["levels"][1]["model"] == effort.BY_ID["medium"].model


def test_confirm_step_reports_the_resolved_plan_and_submits_nothing(console, plans):
    status, _, body = post_goal(console, {"goal": "Summarise", "effort": "medium"})
    d = json.loads(body)
    assert status == 200 and d["needs_confirm"] is True
    assert (d["effort"], d["model"], d["budget"]) == ("medium", effort.BY_ID["medium"].model, 0.30)
    assert plans == []


def test_confirmed_goal_runs_with_the_chosen_plan(console, plans):
    status, _, body = post_goal(console, {"goal": "Summarise", "effort": "high", "confirm": "1"})
    assert status == 200 and json.loads(body)["run"] == "goal_ab12cd34"
    assert plans == [effort.Plan("high", effort.BY_ID["high"].model, 1.0)]


def test_no_effort_uses_the_default_not_high(console, plans, monkeypatch):
    monkeypatch.delenv("OC_EFFORT_DEFAULT", raising=False)
    post_goal(console, {"goal": "Summarise", "confirm": "1"})
    assert plans[0].effort == "low"


def test_advanced_override_reaches_the_plan(console, plans):
    m = effort.models()[0]
    post_goal(console, {"goal": "Summarise", "effort": "high", "model": m, "budget": "0.2", "confirm": "1"})
    assert plans == [effort.Plan("high", m, 0.2)]


@pytest.mark.parametrize(
    "extra",
    [{"effort": "ultra"}, {"model": "anthropic/claude-opus-4"}, {"budget": "99"}, {"budget": "-1"}, {"budget": "x"}],
)
def test_invalid_effort_model_or_budget_is_a_422_and_never_submits(console, plans, extra):
    status, _, body = post_goal(console, {"goal": "Summarise", "confirm": "1", **extra})
    assert status == 422 and "error" in json.loads(body)
    assert plans == []


def wait_for(pred, tries=200):
    got = None
    for _ in range(tries):
        got = pred()
        if got:
            return got
        time.sleep(0.05)
    return got


TWO_STEPS = '[{"id":"a","title":"first","prompt":"p1"},{"id":"b","title":"second","prompt":"p2","deps":["a"]}]'


def test_a_run_pauses_at_its_budget_and_says_so(monkeypatch, tmp_path):
    monkeypatch.setenv("OC_GOALS_DIR", str(tmp_path))
    paid = Usage("m", 1, 1, 0.02)
    rid = goalruns.submit(
        "Two steps", lambda p: TWO_STEPS, lambda t: WorkResult("out", paid), by="t", effort="low", model="m", budget=0.01
    )
    got = wait_for(lambda: (g := goalruns.get_run(rid)) and g["effort"]["paused"] and g)
    assert got["effort"] == {"id": "low", "model": "m", "budget": 0.01, "spent": 0.02, "paused": True}
    states = {s["subtask"]: s["state"] for s in got["subtasks"]}
    assert states["a"] == "DONE" and states.get("b") != "DONE"  # second step was held, not failed


def test_a_run_under_budget_finishes_and_reports_spend(monkeypatch, tmp_path):
    monkeypatch.setenv("OC_GOALS_DIR", str(tmp_path))
    rid = goalruns.submit(
        "Two steps",
        lambda p: TWO_STEPS,
        lambda t: WorkResult("out", Usage("m", 1, 1, 0.001)),
        by="t",
        effort="medium",
        model="m",
        budget=0.30,
    )
    got = wait_for(lambda: (g := goalruns.get_run(rid)) and len(g["subtasks"]) == 2 and all(
        s["state"] == "DONE" for s in g["subtasks"]) and g)
    assert got["effort"]["paused"] is False and got["effort"]["spent"] == pytest.approx(0.002)


def test_a_run_without_a_budget_has_no_effort_block_and_no_ledger(monkeypatch, tmp_path):
    monkeypatch.setenv("OC_GOALS_DIR", str(tmp_path))
    rid = goalruns.submit("One", lambda p: '[{"id":"a","title":"t","prompt":"p"}]', lambda t: "x", by="t")
    got = wait_for(lambda: (g := goalruns.get_run(rid)) and g["subtasks"] and g["subtasks"][0]["state"] == "DONE" and g)
    assert "effort" not in got and not (tmp_path / rid / "cost.ndjson").exists()


def test_resume_keeps_the_runs_model_and_cap(monkeypatch, tmp_path):
    """A run interrupted before any step ran resumes with its own model and its own ledger."""
    monkeypatch.setenv("OC_GOALS_DIR", str(tmp_path))
    started = []
    monkeypatch.setattr(goalruns, "_start_drive", lambda orch, rid, wfn: started.append((orch, rid, wfn)))
    rid = goalruns.submit(
        "Two steps", lambda p: TWO_STEPS, lambda t: "x", by="t", effort="medium", model="model-x", budget=0.30
    )
    started.clear()
    goalruns._live.clear()
    seen = []

    def factory(model):
        seen.append(model)
        return lambda t: "from-factory"

    assert goalruns.resume(lambda t: "default", factory) == 1
    orch, got_rid, wfn = started[0]
    assert seen == ["model-x"] and wfn({}) == "from-factory" and got_rid == rid
    assert orch.ledger.state(rid).cap == 0.30


def test_resume_without_an_effort_uses_the_plain_worker_and_no_ledger(monkeypatch, tmp_path):
    monkeypatch.setenv("OC_GOALS_DIR", str(tmp_path))
    started = []
    monkeypatch.setattr(goalruns, "_start_drive", lambda orch, rid, wfn: started.append((orch, rid, wfn)))
    goalruns.submit("Two steps", lambda p: TWO_STEPS, lambda t: "x", by="t")
    started.clear()
    goalruns._live.clear()
    assert goalruns.resume(lambda t: "default", lambda m: pytest.fail("no model recorded")) == 1
    orch, _, wfn = started[0]
    assert wfn({}) == "default" and orch.ledger is None
