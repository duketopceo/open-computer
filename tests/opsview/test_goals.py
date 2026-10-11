"""Goal surface: list, new form, confirm step, submit + run detail.

Submission is patched — these tests never call a model or spawn a worker.
The run-page render is exercised against a registry populated on disk.
"""

import pytest

from services.opsview import goalruns, server


@pytest.fixture
def submitted(monkeypatch):
    rec = []

    def fake(goal, who):
        rec.append((goal, who))
        return "goal_ab12cd34"

    monkeypatch.setattr(server, "_submit_goal", fake)
    return rec


def test_goals_pages_render(console):
    assert console.get("/goals")[0] == 200
    status, _, html = console.get("/goals/new")
    assert status == 200 and 'name="goal"' in html and 'name="csrf"' in html
    status, _, _ = console.get("/goals/goal_00000000")
    assert status == 404


def test_goal_post_needs_confirm_then_redirects(console, submitted):
    status, _, html = console.post("/goals", {"goal": "Summarise the audit log"})
    assert status == 200 and submitted == []
    assert "Run this goal?" in html and "openrouter.ai" in html and "Summarise the audit log" in html
    status, headers, _ = console.post("/goals", {"goal": "Summarise the audit log", "confirm": "1"})
    assert status == 303 and headers["Location"] == "/goals/goal_ab12cd34"
    assert submitted == [("Summarise the audit log", "luke@test")]


def test_goal_post_rejects_empty_and_overlong(console, submitted):
    assert console.post("/goals", {"goal": "   ", "confirm": "1"})[0] == 400
    assert console.post("/goals", {"goal": "x" * 4001, "confirm": "1"})[0] == 400
    assert submitted == []


def test_run_list_and_detail_read_disk(console, monkeypatch, tmp_path):
    import time
    monkeypatch.setenv("OC_GOALS_DIR", str(tmp_path))
    rid = goalruns.submit(
        "Do two things",
        lambda prompt: '[{"id":"a","title":"first","prompt":"p1"},{"id":"b","title":"second","prompt":"p2","deps":["a"]}]',
        lambda task: f"out:{task['prompt']}",
        by="test",
    )
    got = None
    for _ in range(200):  # the driver is a daemon thread — poll disk until both settle
        got = goalruns.get_run(rid)
        if got and len(got["subtasks"]) == 2 and all(s["state"] in ("DONE", "FAILED") for s in got["subtasks"]):
            break
        time.sleep(0.05)
    assert [s["state"] for s in got["subtasks"]] == ["DONE", "DONE"]
    assert got["worker_failures"] == []
    status, _, html = console.get("/goals")
    assert status == 200 and rid in html and "Do two things" in html and "DONE" in html
    status, _, html = console.get(f"/goals/{rid}")
    assert status == 200 and "out:p2" in html and "first" in html


def test_resume_recovers_interrupted_run(console, monkeypatch, tmp_path):
    """A restart mid-run must not orphan undispatched subtasks (OC-4)."""
    import time
    from services.orchestrator import Orchestrator
    monkeypatch.setenv("OC_GOALS_DIR", str(tmp_path))
    orig = goalruns._start_drive
    monkeypatch.setattr(goalruns, "_start_drive", lambda *a: None)
    engine = lambda prompt: '[{"id":"a","title":"first","prompt":"p1"},{"id":"b","title":"second","prompt":"p2","deps":["a"]}]'
    wfn = lambda task: f"out:{task['prompt']}"
    rid = goalruns.submit("Do two things", engine, wfn, by="test")
    orch = Orchestrator(tmp_path / rid)  # a fresh process only sees disk
    assert orch.recover(rid)
    orch.work(wfn)                       # 'a' completes; 'b' dispatches pending
    goalruns._live.clear()               # crash: all in-memory state gone
    monkeypatch.setattr(goalruns, "_start_drive", orig)
    assert goalruns.resume(wfn) == 1
    got = None
    for _ in range(200):
        got = goalruns.get_run(rid)
        if got and len(got["subtasks"]) == 2 and all(s["state"] in ("DONE", "FAILED") for s in got["subtasks"]):
            break
        time.sleep(0.05)
    assert [s["state"] for s in got["subtasks"]] == ["DONE", "DONE"]
    assert goalruns.resume(wfn) == 0     # terminal runs never re-drive


def test_resume_leaves_preplan_runs_literal(console, monkeypatch, tmp_path):
    """No plan.json (pre-upgrade or mid-decompose crash) — disk stays truth."""
    monkeypatch.setenv("OC_GOALS_DIR", str(tmp_path))
    d = tmp_path / "goal_deadbeef"
    (d / "run").mkdir(parents=True)
    (d / "orchestrator.ndjson").write_text(
        '{"event":"run_submit","run":"goal_deadbeef","goal":"x","subtasks":1}\n')
    assert goalruns.resume(lambda task: None) == 0
    assert goalruns.list_runs()[0]["states"]["QUEUED"] == 1
