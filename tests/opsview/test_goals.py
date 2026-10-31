"""Goal surface: list, new form, confirm step, submit + run detail.

Submission is patched — these tests never call a model or spawn a worker.
The run-page render is exercised against a registry populated on disk.
"""

import json

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
    assert console.get("/goals")[0] == 200  # React shell
    status, headers, _ = console.get("/goals/new")
    assert status == 302 and headers["Location"] == "/"  # composer lives at /
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
        lambda prompt: (
            '[{"id":"a","title":"first","prompt":"p1"},{"id":"b","title":"second","prompt":"p2","deps":["a"]}]'
        ),
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
    status, _, body = console.get("/api/runs")
    runs = json.loads(body)["runs"]
    row = next(r for r in runs if r["run"] == rid)
    assert row["goal"] == "Do two things" and row["states"]["DONE"] == 2
    status, _, body = console.get(f"/api/runs/{rid}")
    detail = json.loads(body)
    assert detail["subtasks"][0]["title"] == "first" and "out:p2" in detail["subtasks"][1]["result"]


def test_resume_recovers_interrupted_run(console, monkeypatch, tmp_path):
    """A restart mid-run must not orphan undispatched subtasks (OC-4)."""
    import time
    from services.orchestrator import Orchestrator

    monkeypatch.setenv("OC_GOALS_DIR", str(tmp_path))
    orig = goalruns._start_drive
    monkeypatch.setattr(goalruns, "_start_drive", lambda *a: None)
    def engine(prompt):
        return '[{"id":"a","title":"first","prompt":"p1"},{"id":"b","title":"second","prompt":"p2","deps":["a"]}]'

    def wfn(task):
        return f"out:{task['prompt']}"
    rid = goalruns.submit("Do two things", engine, wfn, by="test")
    orch = Orchestrator(tmp_path / rid)  # a fresh process only sees disk
    assert orch.recover(rid)
    orch.work(wfn)  # 'a' completes; 'b' dispatches pending
    goalruns._live.clear()  # crash: all in-memory state gone
    monkeypatch.setattr(goalruns, "_start_drive", orig)
    assert goalruns.resume(wfn) == 1
    got = None
    for _ in range(200):
        got = goalruns.get_run(rid)
        if got and len(got["subtasks"]) == 2 and all(s["state"] in ("DONE", "FAILED") for s in got["subtasks"]):
            break
        time.sleep(0.05)
    assert [s["state"] for s in got["subtasks"]] == ["DONE", "DONE"]
    assert goalruns.resume(wfn) == 0  # terminal runs never re-drive


def test_resume_leaves_preplan_runs_literal(console, monkeypatch, tmp_path):
    """No plan.json (pre-upgrade or mid-decompose crash) — disk stays truth."""
    monkeypatch.setenv("OC_GOALS_DIR", str(tmp_path))
    d = tmp_path / "goal_deadbeef"
    (d / "run").mkdir(parents=True)
    (d / "orchestrator.ndjson").write_text('{"event":"run_submit","run":"goal_deadbeef","goal":"x","subtasks":1}\n')
    assert goalruns.resume(lambda task: None) == 0
    assert goalruns.list_runs()[0]["states"]["QUEUED"] == 1


def test_failed_decompose_renders_visible_run(console, monkeypatch, tmp_path):
    """A planner exception must not vanish: run_failed row, FAILED rail
    chip, and the real error on the run page — never a silent 500."""
    monkeypatch.setenv("OC_GOALS_DIR", str(tmp_path))

    def boom(prompt):
        raise RuntimeError("planner endpoint unreachable")

    rid = goalruns.submit("Will fail", boom, lambda task: "x", by="test")
    got = goalruns.get_run(rid)
    assert got["error"] == "planner endpoint unreachable"
    assert got["error_kind"] == "RuntimeError"
    listing = goalruns.list_runs()
    assert listing[0]["run"] == rid and listing[0]["states"]["FAILED"] == 1
    status, _, body = console.get(f"/api/runs/{rid}")
    assert json.loads(body)["error"] == "planner endpoint unreachable"
    status, _, body = console.get("/api/runs")
    row = next(r for r in json.loads(body)["runs"] if r["run"] == rid)
    assert row["goal"] == "Will fail" and row["states"]["FAILED"] == 1


def test_worker_fail_carries_kind_and_hint(console, monkeypatch, tmp_path):
    import time
    from services.orchestrator.engines import LocalModelUnavailable

    monkeypatch.setenv("OC_GOALS_DIR", str(tmp_path))

    def wfn(task):
        raise LocalModelUnavailable(
            "local model endpoints unreachable: :8080 refused", hint="start llama-local.service"
        )

    rid = goalruns.submit(
        "One task",
        lambda prompt: '[{"id":"a","title":"t","prompt":"p"}]',
        wfn,
        by="test",
    )
    got = None
    for _ in range(200):
        got = goalruns.get_run(rid)
        if got and got["subtasks"] and got["subtasks"][0]["state"] == "FAILED":
            break
        time.sleep(0.05)
    sub = got["subtasks"][0]
    assert sub["state"] == "FAILED"
    assert sub["error_kind"] == "LocalModelUnavailable"
    assert ":8080 refused" in sub["error"] and sub["hint"] == "start llama-local.service"
    status, _, body = console.get(f"/api/runs/{rid}")
    sub = json.loads(body)["subtasks"][0]
    assert sub["error_kind"] == "LocalModelUnavailable" and sub["hint"] == "start llama-local.service"


def _write_run(base, rid, rows, thinking=None):
    import json

    d = base / rid
    (d / "run").mkdir(parents=True)
    (d / "orchestrator.ndjson").write_text("".join(json.dumps(r) + "\n" for r in rows))
    if thinking is not None:
        (d / "thinking.ndjson").write_text("".join(json.dumps(r) + "\n" for r in thinking))
    return d


def test_run_detail_carries_start_time_per_step(console, monkeypatch, tmp_path):
    import time

    monkeypatch.setenv("OC_GOALS_DIR", str(tmp_path))
    rid = goalruns.submit(
        "Timed", lambda prompt: '[{"id":"a","title":"first","prompt":"p1"}]', lambda task: "out", by="test"
    )
    got = None
    for _ in range(200):
        got = goalruns.get_run(rid)
        if got and got["subtasks"] and got["subtasks"][0]["state"] == "DONE":
            break
        time.sleep(0.05)
    (s,) = got["subtasks"]
    assert s["ts"] <= s["started_ts"] <= s["finished_ts"]


def test_old_runs_without_start_or_thinking_still_render(console, monkeypatch, tmp_path):
    monkeypatch.setenv("OC_GOALS_DIR", str(tmp_path))
    _write_run(tmp_path, "goal_01d01d00", [
        {"event": "run_submit", "run": "goal_01d01d00", "goal": "old", "subtasks": 1, "ts": 10.0},
        {"event": "dispatch", "run": "goal_01d01d00", "task": "t1", "subtask": "a", "ts": 11.0},
        {"event": "worker_done", "task": "t1", "ts": 15.0},
    ])
    got = goalruns.get_run("goal_01d01d00")
    (s,) = got["subtasks"]
    assert "started_ts" not in s and "thinking" not in s and "plan_thinking" not in got
    assert console.get("/api/runs/goal_01d01d00")[0] == 200


def test_thinking_rows_attach_to_steps_and_plan(console, monkeypatch, tmp_path):
    monkeypatch.setenv("OC_GOALS_DIR", str(tmp_path))
    rid = "goal_7e1ab000"
    _write_run(tmp_path, rid, [
        {"event": "run_submit", "run": rid, "goal": "g", "subtasks": 2, "ts": 10.0},
        {"event": "dispatch", "run": rid, "task": "t1", "subtask": "a", "ts": 11.0},
        {"event": "worker_start", "run": rid, "task": "t1", "subtask": "a", "ts": 12.0},
        {"event": "worker_done", "task": "t1", "ts": 14.0},
        {"event": "dispatch", "run": rid, "task": "t2", "subtask": "b", "ts": 15.0},
        {"event": "worker_fail", "run": rid, "task": "t2", "subtask": "b", "kind": "DependencyFailed",
         "error": "dep failed", "ts": 15.0},
    ], thinking=[
        {"run": rid, "task": "", "subtask": "_plan", "ts": 10.5, "text": "plan reasons"},
        {"run": rid, "task": "t1", "subtask": "a", "ts": 13.9, "text": "step reasons"},
    ])
    got = goalruns.get_run(rid)
    a, b = got["subtasks"]
    assert got["plan_thinking"] == "plan reasons"
    assert a["thinking"] == "step reasons" and a["started_ts"] == 12.0 and a["finished_ts"] == 14.0
    assert "started_ts" not in b and b["finished_ts"] == 15.0 and "thinking" not in b  # tombstone never ran


def test_reclaimed_step_uses_latest_start_and_joins_thinking(console, monkeypatch, tmp_path):
    monkeypatch.setenv("OC_GOALS_DIR", str(tmp_path))
    rid = "goal_2c1a1e00"
    _write_run(tmp_path, rid, [
        {"event": "run_submit", "run": rid, "goal": "g", "subtasks": 1, "ts": 10.0},
        {"event": "dispatch", "run": rid, "task": "t1", "subtask": "a", "ts": 11.0},
        {"event": "worker_start", "run": rid, "task": "t1", "subtask": "a", "ts": 12.0},
        {"event": "worker_start", "run": rid, "task": "t1", "subtask": "a", "ts": 80.0},
        {"event": "worker_done", "task": "t1", "ts": 90.0},
    ], thinking=[
        {"run": rid, "task": "t1", "subtask": "a", "ts": 70.0, "text": "attempt one"},
        {"run": rid, "task": "t1", "subtask": "a", "ts": 89.0, "text": "attempt two"},
    ])
    (s,) = goalruns.get_run(rid)["subtasks"]
    assert s["started_ts"] == 80.0
    assert s["thinking"] == "attempt one\n\nattempt two"
