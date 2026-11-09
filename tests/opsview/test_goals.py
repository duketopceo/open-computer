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

    def fake(goal, who, plan):
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


# --- sandbox boot retried with backoff and surfaced as a wait (KTD6, AE2) ---


class _BootPlane:
    """Fake control plane: the first `fail_execs` exec calls refuse to boot, later ones run."""

    def __init__(self, fail_execs):
        self.fail_execs = fail_execs
        self.execs = 0
        self.plane = self

    def sandboxes_available(self):
        return True

    def make_sandbox(self, **kw):
        return self

    def exec(self, cmd):
        from services.execplane.client import ExecResult, SandboxUnavailable

        self.execs += 1
        if self.execs <= self.fail_execs:
            raise SandboxUnavailable("sandbox boot failed: RuntimeError: backend refused to boot")
        return ExecResult(0, "ran\n", "")

    def close(self):
        pass


@pytest.fixture
def boot_run(monkeypatch, tmp_path):
    """-> (start(subtasks_json, fail_execs), work, advance, model_calls): a stalled-driver run whose work_fn is the
    real sandbox_work over a fake plane, with the runner's clock and the engine's sleep under test control."""
    from services.orchestrator import Orchestrator, engines, runner

    monkeypatch.setenv("OC_GOALS_DIR", str(tmp_path))
    monkeypatch.setattr(goalruns, "_start_drive", lambda *a: None)
    now = [1000.0]
    monkeypatch.setattr(runner, "_clock", lambda: now[0])
    monkeypatch.setattr(engines, "_sleep", lambda s: None)
    model_calls = []

    def chat(url, model, prompt, **kw):
        model_calls.append(prompt)
        return "```bash\necho hi\n```", 0.0

    monkeypatch.setattr(engines, "_chat", chat)
    state = {}

    def start(subtasks, fail_execs):
        plane = _BootPlane(fail_execs)
        state["plane"] = plane

        def wfn(task):
            if task["subtask"] == "a":
                return engines.sandbox_work(task, api_key="k", plane=plane)
            return "plain"

        rid = goalruns.submit("Boot", lambda p: subtasks, wfn, by="test")
        orch = Orchestrator(tmp_path / rid)
        assert orch.recover(rid)
        state.update(rid=rid, orch=orch, wfn=wfn)
        return rid

    def work():
        return state["orch"].work(state["wfn"])

    def advance(s):
        now[0] += s

    yield start, work, advance, model_calls, state
    engines._RUN_SANDBOXES.clear()


_ONE = json.dumps([{"id": "a", "title": "first", "prompt": "p1"}])


def test_boot_failing_twice_then_booting_finishes_with_two_requeue_rows(boot_run):
    from services.orchestrator import runner

    start, work, advance, model_calls, st = boot_run
    rid = start(_ONE, fail_execs=4)  # attempts 1 and 2 each fail the exec and its in-step re-create
    work()
    sub = goalruns.get_run(rid)["subtasks"][0]
    assert sub["state"] == "QUEUED"
    assert sub["waiting_on"]["kind"] == "sandbox_boot"
    assert sub["waiting_on"]["attempt"] == 1
    assert sub["waiting_on"]["max"] == runner.MAX_STEP_ATTEMPTS
    assert "backend refused to boot" in sub["waiting_on"]["detail"]
    advance(runner.BOOT_RETRY_DELAY_S)
    work()
    advance(runner.BOOT_RETRY_DELAY_S)
    work()
    got = goalruns.get_run(rid)
    assert got["subtasks"][0]["state"] == "DONE"
    assert "waiting_on" not in got["subtasks"][0]
    rows = [json.loads(line) for line in (st["orch"].root / "orchestrator.ndjson").read_text().splitlines()]
    requeues = [r for r in rows if r["event"] == "worker_requeue"]
    assert [(r["reason"], r["attempt"]) for r in requeues] == [("sandbox_boot", 1), ("sandbox_boot", 2)]
    assert [r["not_before"] for r in requeues] == [1000.0 + runner.BOOT_RETRY_DELAY_S, 1000.0 + 2 * runner.BOOT_RETRY_DELAY_S]
    assert len(model_calls) == 1  # the cached script served attempts 2 and 3


def test_boot_failing_every_attempt_fails_after_max_step_attempts(boot_run):
    from services.orchestrator import runner

    start, work, advance, model_calls, st = boot_run
    rid = start(_ONE, fail_execs=10_000)
    for _ in range(runner.MAX_STEP_ATTEMPTS + 2):
        work()
        advance(runner.BOOT_RETRY_DELAY_S)
    sub = goalruns.get_run(rid)["subtasks"][0]
    assert sub["state"] == "FAILED"
    assert sub["error_kind"] == "SandboxWorkerUnavailable"
    assert "backend refused to boot" in sub["error"]
    rows = (st["orch"].root / "orchestrator.ndjson").read_text().splitlines()
    assert sum('"worker_start"' in r for r in rows) == runner.MAX_STEP_ATTEMPTS


def test_requeued_step_waits_for_not_before_while_others_run(boot_run):
    from services.orchestrator import runner

    start, work, advance, model_calls, st = boot_run
    subs = json.dumps(
        [
            {"id": "a", "title": "first", "prompt": "p1"},
            {"id": "b", "title": "second", "prompt": "p2"},
            {"id": "c", "title": "third", "prompt": "p3", "deps": ["b"]},
        ]
    )
    rid = start(subs, fail_execs=2)
    work()  # a requeues, b completes, c dispatches
    plane = st["plane"]
    execs_after_first = plane.execs
    advance(runner.BOOT_RETRY_DELAY_S - 1)
    work()  # a is still backing off: only c is claimed
    got = {s["subtask"]: s["state"] for s in goalruns.get_run(rid)["subtasks"]}
    assert got == {"a": "QUEUED", "b": "DONE", "c": "DONE"}
    assert plane.execs == execs_after_first  # a was not touched
    assert 0 < st["orch"].retry_delay() <= 1
    advance(1)
    work()
    assert goalruns.get_run(rid)["subtasks"][0]["state"] == "DONE"


def test_drive_loop_waits_out_the_backoff_instead_of_stranding_the_run(boot_run):
    from services.orchestrator import runner

    start, work, advance, model_calls, st = boot_run
    rid = start(_ONE, fail_execs=2)
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        advance(s)

    goalruns._drive(st["orch"], rid, st["wfn"], sleep=sleep)
    assert goalruns.get_run(rid)["subtasks"][0]["state"] == "DONE"
    assert len(sleeps) == 1 and 0 < sleeps[0] <= runner.BOOT_RETRY_DELAY_S


def test_twice_invalid_plan_renders_failed_with_the_validator_message(console, monkeypatch, tmp_path):
    monkeypatch.setenv("OC_GOALS_DIR", str(tmp_path))
    bad = json.dumps([{"id": "t1", "title": "A", "prompt": "a", "latency": "whenever"}])
    calls = []

    def planner(prompt):
        calls.append(prompt)
        return bad

    rid = goalruns.submit("Bad enums", planner, lambda task: "x", by="test")
    got = goalruns.get_run(rid)
    assert len(calls) == 2
    assert got["error_kind"] == "DecomposeError"
    assert "t1" in got["error"] and "whenever" in got["error"]
    assert goalruns.list_runs()[0]["states"]["FAILED"] == 1


def test_worker_fail_carries_kind_and_hint(console, monkeypatch, tmp_path):
    import time
    from services.orchestrator.engines import WorkerUnavailable

    monkeypatch.setenv("OC_GOALS_DIR", str(tmp_path))

    def wfn(task):
        raise WorkerUnavailable("OPENROUTER_API_KEY is not set", hint="set the key and restart")

    rid = goalruns.submit(
        "One task",
        lambda prompt: '[{"id":"a","title":"t","prompt":"p"}]',
        wfn,
        by="test",
    )
    got = None
    for _ in range(200):
        got = goalruns.get_run(rid)
        # The mailbox says FAILED a moment before the worker_fail audit row lands: wait for both.
        if got and got["subtasks"] and got["subtasks"][0]["state"] == "FAILED" and "error_kind" in got["subtasks"][0]:
            break
        time.sleep(0.05)
    sub = got["subtasks"][0]
    assert sub["state"] == "FAILED"
    assert sub["error_kind"] == "WorkerUnavailable"
    assert "OPENROUTER_API_KEY" in sub["error"] and sub["hint"] == "set the key and restart"
    status, _, body = console.get(f"/api/runs/{rid}")
    sub = json.loads(body)["subtasks"][0]
    assert sub["error_kind"] == "WorkerUnavailable" and sub["hint"] == "set the key and restart"


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
    assert b["started_ts"] == b["finished_ts"] == 15.0 and b["skipped"] is True and "thinking" not in b  # never ran


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


def test_steps_expose_router_and_worker_and_cost_is_worker_plus_router(console, monkeypatch, tmp_path):
    monkeypatch.setenv("OC_GOALS_DIR", str(tmp_path))
    rid = "goal_70a0b000"
    fb = {"engine": "decider", "kind": "EngineDown", "status": 404, "reason": "No endpoints found"}
    worker = {"model": "deepseek/deepseek-v4-pro", "provider": "DeepInfra", "tokens_in": 30, "tokens_out": 12,
              "tokens_reasoning": 0, "cost": 0.0102635}
    _write_run(tmp_path, rid, [
        {"event": "run_submit", "run": rid, "goal": "g", "subtasks": 3, "ts": 10.0},
        {"event": "dispatch", "run": rid, "task": "t1", "subtask": "a", "cost": 0.000001, "ts": 11.0,
         "router": {"engine": "jev", "model": "typesafe/jev-1.13", "confidence": 0.8, "ms": 250.0, "fallbacks": [fb]}},
        {"event": "worker_start", "run": rid, "task": "t1", "subtask": "a", "ts": 12.0},
        {"event": "worker_requeue", "task": "t1", "reason": "boot", "ts": 13.0, "worker": {**worker, "cost": 0.002}},
        {"event": "worker_done", "task": "t1", "ts": 14.0, "worker": worker},
        {"event": "dispatch", "run": rid, "task": "t2", "subtask": "b", "cost": 0.0, "ts": 15.0},
        {"event": "worker_fail", "task": "t2", "kind": "RuntimeError", "error": "rc 1", "ts": 16.0,
         "worker": {**worker, "cost": 0.004}},
        {"event": "dispatch", "run": rid, "task": "t3", "subtask": "c", "cost": 0.5, "ts": 17.0},
    ])
    a, b, c = goalruns.get_run(rid)["subtasks"]
    assert a["router"]["fallbacks"] == [fb] and a["router"]["engine"] == "jev"
    assert a["worker"] == worker  # the attempt that finished
    assert a["cost"] == pytest.approx(0.000001 + 0.002 + 0.0102635)  # the retried attempt was paid for too
    assert b["worker"]["cost"] == 0.004 and b["cost"] == pytest.approx(0.004)
    assert "router" not in b and "worker" not in c and c["cost"] == 0.5  # old rows: unchanged


def test_router_ms_is_reported_in_milliseconds(monkeypatch, tmp_path):
    from services.router import RouteDecision

    class R:
        def route(self, q):
            return RouteDecision("cloud", "mid", "jev", 0.9, 0.0, 4.2)

    import services.router as router_pkg

    monkeypatch.setattr(router_pkg, "default_router", lambda *a, **k: R())
    monkeypatch.setenv("OC_DATA_DIR", str(tmp_path))
    assert server._route("hi")["ms"] == 4


def test_dependent_failed_step_is_skipped_with_no_duration(console, monkeypatch, tmp_path):
    monkeypatch.setenv("OC_GOALS_DIR", str(tmp_path))
    rid = "goal_5e1b0000"
    _write_run(tmp_path, rid, [
        {"event": "run_submit", "run": rid, "goal": "g", "subtasks": 2, "ts": 10.0},
        {"event": "dispatch", "run": rid, "task": "t1", "subtask": "a", "ts": 11.0},
        {"event": "worker_start", "run": rid, "task": "t1", "subtask": "a", "ts": 12.0},
        {"event": "worker_fail", "run": rid, "task": "t1", "subtask": "a", "kind": "RuntimeError",
         "error": "sandbox exec rc=1", "ts": 14.0},
        {"event": "dispatch", "run": rid, "task": "t2", "subtask": "b", "ts": 40.0},
        {"event": "worker_fail", "run": rid, "task": "t2", "subtask": "b", "kind": "DependencyFailed",
         "error": "dep 'a' failed — cannot run", "ts": 40.5},
    ])
    a, b = goalruns.get_run(rid)["subtasks"]
    assert "skipped" not in a and a["started_ts"] == 12.0
    assert b["skipped"] is True and b["started_ts"] == b["finished_ts"] == 40.5
