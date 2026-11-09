"""Chat threads: multi-turn turns, iteration after a run, and delete/archive. A scripted model and a fake run
starter stand in for OpenRouter and the orchestrator: no model call, no sandbox."""

import json
import time

import pytest

from services.opsview import goalruns, server, threads
from services.orchestrator import chat

PLAN = {"effort": "low", "model": "m-low", "budget": 0.1}


def decision(action, text="", goal=""):
    return json.dumps({"action": action, "text": text, **({"goal": goal} if goal else {})})


class Script:
    """A model that answers from a list and records every conversation it was shown."""

    def __init__(self, *answers):
        self.answers, self.seen = list(answers), []

    def __call__(self, messages):
        self.seen.append(messages)
        return self.answers.pop(0)


def view(rid, state="DONE", result="out", **kw):
    return {"run": rid, "declared": 1, "subtasks": [{"subtask": "s", "title": "step", "state": state, "result": result}], **kw}


@pytest.fixture
def tdir(tmp_path, monkeypatch):
    monkeypatch.setenv("OC_THREADS_DIR", str(tmp_path / "threads"))
    monkeypatch.setenv("OC_GOALS_DIR", str(tmp_path / "goals"))
    return tmp_path


def deps(engine, views=None, stops=None):
    started, views = [], views if views is not None else {}

    def start(goal):
        rid = f"goal_{len(started):08x}"
        started.append(goal)
        views.setdefault(rid, view(rid))
        return rid

    d = threads.Deps(engine=engine, start_run=start, load_run=views.get, wait_run=lambda rid: (stops or {}).get(rid, "done"))
    return d, started


# --- reading the model's answer
@pytest.mark.parametrize(
    "raw,want",
    [
        ('{"action":"reply","text":"hi"}', chat.Decision("reply", "hi")),
        ('```json\n{"action":"ask","text":"which one?"}\n```', chat.Decision("ask", "which one?")),
        ('Sure! {"action":"run","text":"on it","goal":"do x"}', chat.Decision("run", "on it", "do x")),
        ('{"action":"run","text":"on it"}', chat.Decision("reply", '{"action":"run","text":"on it"}')),  # no goal: not a run
        ("just words", chat.Decision("reply", "just words")),
        ("", chat.Decision("reply", "I did not get an answer back. Try again.")),
    ],
)
def test_parse_decision_never_loses_text(raw, want):
    assert chat.parse_decision(raw) == want


# --- turns
def test_plain_question_gets_a_reply_and_starts_no_run(tdir):
    t = threads.create("what is 2+2?", PLAN)
    d, started = deps(Script(decision("reply", "4")))
    threads.run_turn(t["id"], d)
    msgs = threads.get(t["id"])["messages"]
    assert [(m["role"], m["text"]) for m in msgs] == [("user", "what is 2+2?"), ("assistant", "4")]
    assert started == []


def test_a_run_is_shown_to_the_model_and_a_bad_result_triggers_a_second_run(tdir):
    t = threads.create("make the report", PLAN)
    views = {"goal_00000000": view("goal_00000000", "FAILED", "", error="x"), "goal_00000001": view("goal_00000001", result="report ok")}
    views["goal_00000000"]["subtasks"][0]["error"] = "ImportError: no module named pandas"
    model = Script(
        decision("run", "Building it.", "write report.md"),
        decision("run", "That failed on a missing module; retrying without it.", "write report.md using only the stdlib"),
        decision("reply", "Done: report.md is written."),
    )
    d, started = deps(model, views)
    threads.run_turn(t["id"], d)
    assert started == ["write report.md", "write report.md using only the stdlib"]
    roles = [m["role"] for m in threads.get(t["id"])["messages"]]
    assert roles == ["user", "assistant", "run", "assistant", "run", "assistant"]
    # the model's second look saw the real failure, not a summary of it
    second = model.seen[1][-1]["content"]
    assert "ImportError: no module named pandas" in second and "[run goal_00000000]" in second


def test_follow_up_message_continues_the_same_thread_with_full_history(tdir):
    t = threads.create("pick a name for the app", PLAN)
    threads.run_turn(t["id"], deps(Script(decision("reply", "How about Lumen?")))[0])
    threads.append(t["id"], "user", "shorter")
    model = Script(decision("reply", "Lum."))
    threads.run_turn(t["id"], deps(model)[0])
    sent = [m["content"] for m in model.seen[0] if m["role"] != "system"]
    assert sent == ["pick a name for the app", "How about Lumen?", "shorter"]
    assert threads.get(t["id"])["messages"][-1]["text"] == "Lum."


def test_ask_is_flagged_and_ends_the_turn(tdir):
    t = threads.create("deploy it", PLAN)
    threads.run_turn(t["id"], deps(Script(decision("ask", "Which environment?")))[0])
    last = threads.get(t["id"])["messages"][-1]
    assert (last["role"], last["ask"]) == ("assistant", True)


def test_runs_per_message_are_capped_and_the_last_call_may_not_start_another(tdir):
    t = threads.create("loop forever", PLAN)
    model = Script(*[decision("run", "again", "g")] * (chat.MAX_PASSES + 1))
    d, started = deps(model)
    threads.run_turn(t["id"], d)
    assert len(started) == chat.MAX_PASSES
    assert chat.FINAL_NOTE in model.seen[-1][-1]["content"]
    last = threads.get(t["id"])["messages"][-1]
    assert last["role"] == "assistant" and last["text"] == "again"


@pytest.mark.parametrize("stop", ["plan", "approval", "paused", "timeout"])
def test_a_run_that_cannot_finish_says_why_and_stops(tdir, stop):
    t = threads.create("go", PLAN)
    d, started = deps(Script(decision("run", "ok", "g")), stops={"goal_00000000": stop})
    threads.run_turn(t["id"], d)
    last = threads.get(t["id"])["messages"][-1]
    assert last["role"] == "assistant" and len(started) == 1
    assert threads._STOP_NOTES[stop] == last["text"]


def test_wait_tells_a_held_plan_from_a_step_approval(tdir, monkeypatch):
    seen = {"r_plan": {"plan": {"state": "planned"}, "subtasks": []},
            "r_step": {"declared": 1, "subtasks": [{"state": "AWAITING-APPROVAL"}]}}
    monkeypatch.setattr(goalruns, "get_run", seen.get)
    assert threads.wait_for_run("r_plan", timeout=1) == "plan"
    assert threads.wait_for_run("r_step", timeout=1) == "approval"
    assert "tell me to carry on" not in threads._STOP_NOTES["plan"]


def test_approving_the_plan_resumes_the_chat_and_it_replies(tdir):
    # First turn stops on the held plan; the resume waits for that run, then the model answers with its result.
    t = threads.create("go", PLAN)
    d, started = deps(Script(decision("run", "on it", "g")), stops={"goal_00000000": "plan"})
    threads.run_turn(t["id"], d)
    assert threads.get(t["id"])["messages"][-1]["text"] == threads._STOP_NOTES["plan"]
    assert threads.thread_for_run("goal_00000000") == t["id"]
    assert threads.thread_for_run("goal_ffffffff") is None
    model = Script(decision("reply", "here it is"))
    d2, started2 = deps(model, views={"goal_00000000": view("goal_00000000")})
    threads.run_turn(t["id"], d2, waiting_on="goal_00000000")
    last = threads.get(t["id"])["messages"][-1]
    assert last["role"] == "assistant" and last["text"] == "here it is"
    assert started2 == []  # no second run: the approved one is reported
    assert "out" in json.dumps(model.seen[0])  # the model saw the finished run's result


def test_a_model_failure_becomes_a_message_not_a_crash(tdir):
    t = threads.create("hi", PLAN)

    def boom(_):
        raise RuntimeError("OPENROUTER_API_KEY is not set")

    threads.run_turn(t["id"], deps(boom)[0])
    last = threads.get(t["id"])["messages"][-1]
    assert last["error"] is True and "OPENROUTER_API_KEY" in last["text"]


def test_only_one_turn_at_a_time(tdir):
    import threading

    t = threads.create("slow", PLAN)
    gate = threading.Event()

    def slow(_):
        gate.wait(5)
        return decision("reply", "late")

    d, _ = deps(slow)
    assert threads.start_turn(t["id"], d) is True
    assert threads.is_working(t["id"]) and threads.start_turn(t["id"], d) is False
    with pytest.raises(threads.ThreadBusy):
        threads.delete(t["id"])
    gate.set()
    for _ in range(100):
        if not threads.is_working(t["id"]):
            break
        time.sleep(0.02)
    assert threads.get(t["id"])["messages"][-1]["text"] == "late"


# --- cleanup
def test_delete_removes_the_thread_and_its_runs_but_nothing_else(tdir):
    t = threads.create("x", PLAN)
    run = goalruns.runs_dir() / "goal_0000000a"
    other = goalruns.runs_dir() / "goal_0000000b"
    for d in (run, other):
        d.mkdir(parents=True)
    threads.append(t["id"], "run", "g", run="goal_0000000a", goal="g")
    assert threads.delete(t["id"]) is True
    assert threads.get(t["id"]) is None and not run.exists() and other.exists()
    assert threads.delete(t["id"]) is False
    assert threads.delete("../../etc/passwd") is False


def test_archive_hides_and_restores_and_clear_spares_working_threads(tdir):
    a, b, c = (threads.create(n, PLAN) for n in "abc")
    threads.archive(a["id"])
    assert {s["id"] for s in threads.list_threads()} == {b["id"], c["id"]}
    assert {s["id"] for s in threads.list_threads(include_archived=True)} == {a["id"], b["id"], c["id"]}
    threads.archive(a["id"], False)
    threads._working.add(c["id"])
    try:
        assert threads.clear_finished() == 2
        assert [s["id"] for s in threads.list_threads(include_archived=True)] == [c["id"]]
    finally:
        threads._working.discard(c["id"])


def test_corrupt_thread_file_is_skipped_not_fatal(tdir):
    t = threads.create("ok", PLAN)
    (threads.threads_dir() / "th_deadbeef.json").write_text("{not json")
    assert [s["id"] for s in threads.list_threads()] == [t["id"]]


# --- HTTP
def test_api_new_thread_starts_a_turn_with_the_effort_and_no_confirm(console, monkeypatch):
    seen = {}
    model = Script(decision("reply", "hello back"))

    def fake_deps(plan, who):
        seen["plan"], seen["who"] = plan, who
        return deps(model)[0]

    monkeypatch.setattr(server, "_turn_deps", fake_deps)
    csrf = console.csrf()
    hdr = {"Content-Type": "application/json", "X-CSRF-Token": csrf}
    post = lambda path, body: console.request("POST", path, body=json.dumps(body), headers=hdr)  # noqa: E731
    s, _, b = post("/api/threads", {"text": "hello", "effort": "medium"})
    assert s == 200
    tid = json.loads(b)["thread"]
    assert seen["plan"].effort == "medium" and seen["who"] == "luke@test"
    for _ in range(100):
        d = json.loads(console.get(f"/api/threads/{tid}")[2])
        if not d["working"]:
            break
        time.sleep(0.02)
    assert [m["text"] for m in d["messages"]] == ["hello", "hello back"] and d["runs"] == {}
    assert [t["id"] for t in json.loads(console.get("/api/threads")[2])["threads"]] == [tid]
    # follow-up
    monkeypatch.setattr(server, "_turn_deps", lambda plan, who: deps(Script(decision("reply", "again")))[0])
    assert post(f"/api/threads/{tid}/messages", {"text": "more"})[0] == 200
    for _ in range(100):
        d = json.loads(console.get(f"/api/threads/{tid}")[2])
        if not d["working"] and len(d["messages"]) == 4:
            break
        time.sleep(0.02)
    assert [m["role"] for m in d["messages"]] == ["user", "assistant", "user", "assistant"]
    # validation
    assert post("/api/threads", {"text": "   "})[0] == 400
    assert post("/api/threads", {"text": "x" * 4001})[0] == 400
    assert post("/api/threads", {"text": "x", "effort": "ultra"})[0] == 422
    assert post("/api/threads/th_00000000/messages", {"text": "x"})[0] == 404
    # cleanup
    assert post(f"/api/threads/{tid}/archive", {})[0] == 200
    assert json.loads(console.get("/api/threads")[2])["threads"] == []
    assert post("/api/threads/clear", {})[0] == 400
    assert json.loads(post("/api/threads/clear", {"confirm": True})[2]) == {"cleared": 1}
    assert console.get(f"/api/threads/{tid}")[0] == 404


def test_thread_runs_leave_the_loose_run_list_and_survive_clear_runs(console):
    base = console.base / "goals"
    base.mkdir(exist_ok=True)
    for rid in ("goal_0000000a", "goal_0000000b"):
        d = base / rid
        d.mkdir()
        (d / "orchestrator.ndjson").write_text(json.dumps({"event": "run_failed", "goal": rid, "error": "x", "ts": 1}) + "\n")
    t = threads.create("x", PLAN)
    threads.append(t["id"], "run", "g", run="goal_0000000a", goal="g")
    ids = lambda q: {r["run"] for r in json.loads(console.get("/api/runs" + q)[2])["runs"]}  # noqa: E731
    assert ids("") == {"goal_0000000a", "goal_0000000b"}  # the operator page still sees everything
    assert ids("?threaded=0") == {"goal_0000000b"}
    hdr = {"Content-Type": "application/json", "X-CSRF-Token": console.csrf()}
    assert json.loads(console.request("POST", "/api/runs/clear", body='{"confirm":true}', headers=hdr)[2]) == {"cleared": 1}
    assert ids("") == {"goal_0000000a"}


def test_a_plan_decision_resumes_the_chat_that_owns_the_run(tdir, monkeypatch):
    t = threads.create("go", PLAN)
    threads.append(t["id"], "run", "g", run="goal_0000abcd", goal="g")
    calls = []
    monkeypatch.setattr(server, "_turn_deps", lambda plan, who: "deps")
    monkeypatch.setattr(threads, "start_turn", lambda tid, d, waiting_on=None: calls.append((tid, waiting_on)) or True)
    assert server.resume_after_plan("goal_0000abcd", "luke")
    assert calls == [(t["id"], "goal_0000abcd")]
    assert not server.resume_after_plan("goal_ffffffff", "luke")  # a run no chat started: nothing to resume
