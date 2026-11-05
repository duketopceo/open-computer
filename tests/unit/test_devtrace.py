"""Dev trace spine: the per-run event stream behind OC_DEV_TRACE / dev.json (U6).

Everything runs against the in-process fake OpenRouter and a fake sandbox plane; no model, microVM or network."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from fake_openrouter import FakeOpenRouter, chat_reply
from oc.events import FileLog, errors
from oc.obs import bind, devtrace
from oc.obs.redact import redact_str
from services.execplane.client import ExecResult
from services.openrouter import chat
from services.opsview import goalruns
from services.orchestrator import Orchestrator, engines
from services.orchestrator.runcfg import create_ledger
from services.placement import Placement
from services.router.router import RouteDecision

ROOT = Path(__file__).resolve().parents[2]
RID = "goal_0123abcd"
STRIPE = "sk_live_" + "Q9x7LmP2" * 3
GOOGLE = "AIza" + "SyD-9tSrke72PouQMnMX_a7eZSW0jkFMBWY"
PEM = "-----BEGIN RSA PRIVATE KEY-----\n" + "\n".join("MIIEowIBAAKCAQEA" + str(i) * 40 for i in range(6)) + (
    "\n-----END RSA PRIVATE KEY-----")


@pytest.fixture
def goals(tmp_path, monkeypatch):
    monkeypatch.setenv("OC_GOALS_DIR", str(tmp_path / "goals"))
    monkeypatch.delenv("OC_DEV_TRACE", raising=False)
    return tmp_path / "goals"


@pytest.fixture
def fake(monkeypatch):
    with FakeOpenRouter() as f:
        monkeypatch.setenv("OC_OPENROUTER_BASE_URL", f.url)
        yield f


def stream(goals: Path, rid: str = RID) -> list[dict]:
    f = goals / rid / devtrace.STREAM
    return [json.loads(ln) for ln in f.read_text().splitlines()] if f.exists() else []


# ---- redaction: whole string first, cap second ----------------------------------------------------------------


@pytest.mark.parametrize("secret", [STRIPE, "sk_test_" + "Ab3dE5gH" * 2, GOOGLE, PEM])
def test_new_secret_shapes_are_redacted(secret):
    assert secret not in redact_str(f"key={secret} end") and "[REDACTED]" in redact_str(f"x {secret} y")


def test_an_unterminated_pem_block_is_redacted_to_the_end():
    out = redact_str("before\n-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC")
    assert out == "before\n[REDACTED]"


@pytest.mark.parametrize("secret", [STRIPE, GOOGLE, PEM])
def test_a_secret_straddling_the_tail_cut_never_leaves_a_fragment(secret):
    after = devtrace.TAIL_BYTES - 10  # the cut falls 10 characters before the secret's end
    raw = "a" * 100 + secret + "\n" + "b" * after
    kept = "\n".join(devtrace.tail(raw))
    assert secret[-10:] not in kept and "PRIVATE KEY" not in kept and "AIza" not in kept and "sk_live_" not in kept
    assert len(kept.encode()) <= devtrace.TAIL_BYTES


def test_a_secret_straddling_the_text_cap_never_leaves_a_fragment():
    out = devtrace.text("a" * 90 + STRIPE + "tail", cap=95)
    assert "sk_live_" not in out and out.endswith("[truncated]")


def test_tail_keeps_the_last_fifty_lines():
    out = devtrace.tail("\n".join(f"line{i}" for i in range(200)))
    assert out[0] == "line150" and out[-1] == "line199" and len(out) == 50 and devtrace.tail("") == []


def test_entity_ids_follow_the_ref_pattern():
    assert devtrace.entity("step", "t1") == "step_t1"
    assert devtrace.entity("step", "Fetch Docs-2") == "step_fetch_docs_2"
    assert devtrace.entity("call", "0123456789abcdef") == "call_0123456789abcdef"


# ---- the gate -------------------------------------------------------------------------------------------------


def test_dev_json_is_read_strictly_and_a_corrupt_file_counts_as_off(goals):
    assert devtrace.read_setting() is False and not goals.parent.exists() or not (goals.parent / "dev.json").exists()
    goals.parent.mkdir(parents=True, exist_ok=True)
    f = goals.parent / "dev.json"
    for bad in ("{not json", '"yes"', "[]", '{"trace": "true"}', '{"trace": 1}', ""):
        f.write_text(bad)
        assert devtrace.read_setting() is False, bad
        assert devtrace.requested() is False
    f.write_text('{"trace": true}')
    assert devtrace.read_setting() is True and devtrace.requested() is True


def test_env_turns_dev_trace_on_without_a_file(goals, monkeypatch):
    monkeypatch.setenv("OC_DEV_TRACE", "1")
    assert devtrace.requested() is True and devtrace.status() == {"trace": False, "env": True, "active": True}
    monkeypatch.setenv("OC_DEV_TRACE", "0")
    assert devtrace.requested() is False


def test_write_setting_is_atomic_and_private(goals):
    devtrace.write_setting(True)
    f = goals.parent / "dev.json"
    assert json.loads(f.read_text()) == {"trace": True} and (f.stat().st_mode & 0o777) == 0o600
    assert [p.name for p in f.parent.iterdir()] == ["dev.json"]  # no temp file left behind


def test_nothing_is_written_for_a_run_without_a_stream(goals, fake):
    (goals / RID).mkdir(parents=True)
    assert devtrace.tracer(run_id=RID) is None
    with bind(run_id=RID, subtask="t1", service="worker"):
        chat("vendor/model", "hi", key="k")
    assert list((goals / RID).iterdir()) == []  # no stream, no lock file


# ---- model calls ----------------------------------------------------------------------------------------------


def test_every_model_call_of_a_traced_run_is_an_llm_call(goals, fake):
    run_dir = goals / RID
    run_dir.mkdir(parents=True)
    devtrace.enable(run_dir)
    fake.chat_replies += [
        chat_reply("hello", cost=0.0042, model="vendor/served", tokens_in=11, tokens_out=7, reasoning="r" * 20_000),
        503,
    ]
    with bind(run_id=RID, subtask="t1", service="worker"):
        chat("vendor/asked", "hi", key="k")
        with pytest.raises(Exception):
            chat("vendor/asked", "again sk-or-v1-leakleakleakleak12", key="k")
    ok, bad = [e for e in stream(goals) if e["type"] == "llm_call"]
    assert all(errors(e) == [] for e in (ok, bad))
    d = ok["data"]
    assert (d["kind"], d["status"], d["step"], d["provider"]) == ("work", "ok", "step_t1", "Acme")
    assert (d["model_requested"], d["model_served"], d["cost_usd"]) == ("vendor/asked", "vendor/served", 0.0042)
    assert (d["tokens_in"], d["tokens_out"], d["tokens_reasoning"]) == (11, 7, 1) and d["latency_ms"] >= 0
    assert len(d["reasoning"]) == devtrace.REASONING_CAP + len("\n… [truncated]") and d["reasoning"].endswith("[truncated]")
    assert bad["data"]["status"] == "error" and bad["data"]["http_status"] == 503
    assert "leakleak" not in json.dumps(bad)


def test_a_call_without_reasoning_carries_no_reasoning_field(goals, fake):
    (goals / RID).mkdir(parents=True)
    devtrace.enable(goals / RID)
    with bind(run_id=RID, service="orchestrator"):
        chat("vendor/model", "plan it", key="k")
    (ev,) = stream(goals)
    assert ev["data"]["kind"] == "plan" and "reasoning" not in ev["data"]


# ---- a whole fake run -----------------------------------------------------------------------------------------


@dataclass
class _Decision(RouteDecision):
    fallbacks: list = field(default_factory=list)


class _Router:
    def route(self, utterance):
        return _Decision("cloud", "cheap", "jev", 0.8, 0.0007, 12.0, "vendor/jev",
                         fallbacks=[{"engine": "decider", "kind": "EngineDown", "status": 404,
                                     "reason": "No endpoints found sk-or-v1-leakleakleakleak12"}])


class _Plane:
    """A control plane whose command fails when the script says `fail`, with secrets on stderr."""

    def sandboxes_available(self):
        return True

    def make_sandbox(self, **kw):
        class SB:
            def exec(self, cmd):
                script = base64.b64decode(cmd.split()[1]).decode()
                if "fail" in script:
                    return ExecResult(returncode=1, stdout="partial\n", stderr=f"E: boom\n{GOOGLE}\nno such file\n")
                return ExecResult(returncode=0, stdout="fine\n", stderr="")

            def close(self):
                pass

        return SB()


PLAN = json.dumps([
    {"id": "t1", "title": "first", "prompt": "do the good thing", "deps": []},
    {"id": "t2", "title": "second", "prompt": "do the bad thing", "deps": []},
])


def drive(run_dir: Path, rid: str, fake, monkeypatch, *, traced: bool, ledger: bool = False) -> Orchestrator:
    monkeypatch.setattr(engines, "_plane", lambda: _Plane())
    def reply(body):  # the sweep claims tasks in an arbitrary order, so answer by what was asked
        asked = body["messages"][0]["content"]
        if "bad thing" in asked:
            return chat_reply(f"```bash\nexport K={STRIPE}\nfail\n```", model="vendor/served-b", cost=0.0033)
        if "good thing" in asked:
            return chat_reply("```bash\necho ok\n```", model="vendor/served-a", cost=0.0021, reasoning="why a")
        return chat_reply(PLAN, model="vendor/planner", reasoning="plan reasons", cost=0.001)

    fake.default_reply = reply
    run_dir.mkdir(parents=True, exist_ok=True)
    if traced:
        devtrace.enable(run_dir)
    cap = create_ledger(run_dir, rid, 1.0, "test") if ledger else None
    orch = Orchestrator(run_dir, router=_Router(), place_fn=lambda *a, **k: Placement("cloud-sandbox", "needs a VM"),
                        ledger=cap)
    orch.submit("Do two things", engines.decompose_engine("vendor/planner", api_key="k"), run_id=rid)
    for _ in range(8):
        if not orch.work(engines.work_fn("vendor/worker", api_key="k")):
            break
    orch.settle_trace(rid)
    return orch


def test_a_traced_run_writes_a_schema_valid_stream_in_order(goals, fake, monkeypatch):
    drive(goals / RID, RID, fake, monkeypatch, traced=True, ledger=True)
    evs = stream(goals)
    assert [e["seq"] for e in evs] == list(range(1, len(evs) + 1))
    assert all(errors(e) == [] and e["goal"] == RID for e in evs), [errors(e) for e in evs if errors(e)]
    kinds = [(e["type"], e["data"].get("id", e["data"].get("status"))) for e in evs]

    def at(t, **match):
        return next(i for i, e in enumerate(evs) if e["type"] == t and all(e["data"].get(k) == v for k, v in match.items()))

    order = [
        at("run", status="running"),
        at("llm_call", id="call_plan"),
        at("step", id="step_t1", status="pending"),
        at("step", id="step_t2", status="pending"),
        at("route", id="route_t2"),
        at("step", id="step_t2", status="running"),
        at("tool_call", id=next(e["data"]["id"] for e in evs if e["type"] == "tool_call"
                                and e["data"].get("rc") == 1), status="error"),
        at("step", id="step_t2", status="failed"),
        at("error", step="step_t2"),
        at("run", status="failed"),
    ]
    assert order == sorted(order), kinds

    route = next(e["data"] for e in evs if e["type"] == "route" and e["data"]["id"] == "route_t2")
    assert (route["engine"], route["model"], route["confidence"], route["placement"]) == ("jev", "vendor/jev", 0.8, "cloud-sandbox")
    assert route["fallbacks"] == [{"engine": "decider", "kind": "EngineDown", "status": 404,
                                   "reason": "No endpoints found [REDACTED]"}]
    work = [e["data"] for e in evs if e["type"] == "llm_call" and e["data"]["kind"] == "work"]
    assert {w["step"]: (w["model_served"], w["cost_usd"]) for w in work} == {
        "step_t1": ("vendor/served-a", 0.0021), "step_t2": ("vendor/served-b", 0.0033)}
    plan_call = next(e["data"] for e in evs if e["type"] == "llm_call" and e["data"]["kind"] == "plan"
                     and e["data"]["id"] != "call_plan")
    assert plan_call["model_served"] == "vendor/planner" and plan_call["reasoning"] == "plan reasons"
    assert json.loads(next(e["data"]["detail"] for e in evs if e["data"].get("id") == "call_plan"))[0]["id"] == "t1"

    done = next(e["data"] for e in evs if e["type"] == "step" and e["data"]["id"] == "step_t1" and e["data"]["status"] == "done")
    assert done["model"] == "vendor/served-a"
    cmd = [e["data"] for e in evs if e["type"] == "tool_call" and e["data"].get("rc") == 1][0]
    assert cmd["name"] == "shell.exec" and cmd["step"] == "step_t2" and cmd["duration_ms"] >= 0
    assert "fail" in cmd["command"] and cmd["stdout_tail"] == ["partial"] and "no such file" in cmd["stderr_tail"]
    assert any(e["type"] == "tool_call" and e["data"]["status"] == "running" for e in evs)
    err = next(e["data"] for e in evs if e["type"] == "error")
    assert err["code"] == "RuntimeError" and "rc=1" in err["message"]
    cost = [e["data"] for e in evs if e["type"] == "cost"]
    assert cost and cost[-1]["cap_usd"] == 1.0 and cost[-1]["spent_usd"] > 0 and "threshold" not in cost[0]


def test_secrets_in_scripts_output_and_errors_never_reach_the_stream(goals, fake, monkeypatch):
    drive(goals / RID, RID, fake, monkeypatch, traced=True)
    raw = (goals / RID / devtrace.STREAM).read_text()
    assert STRIPE not in raw and GOOGLE not in raw and "leakleak" not in raw and "[REDACTED]" in raw


def test_a_run_submitted_with_dev_trace_off_changes_nothing(goals, fake, monkeypatch, tmp_path):
    off, on = tmp_path / "off", tmp_path / "on"
    drive(off / RID, RID, fake, monkeypatch, traced=False)
    bodies = lambda: sorted(json.dumps(r["body"], sort_keys=True) for r in fake.requests)  # noqa: E731 - the sweep order varies
    off_requests = bodies()
    assert not any(p.name.startswith("events") for p in (off / RID).rglob("*"))
    assert not (off / RID / devtrace.STREAM).exists()
    fake.requests.clear()
    drive(on / RID, RID, fake, monkeypatch, traced=True)
    assert bodies() == off_requests  # same provider bodies: tracing adds no field
    assert (on / RID / devtrace.STREAM).exists()
    rows = lambda d: sorted(json.loads(ln)["event"] for ln in (d / RID / "orchestrator.ndjson").read_text().splitlines())  # noqa: E731
    assert rows(off) == rows(on)  # the same audit events, none added or dropped
    submit = next(json.loads(ln) for ln in (on / RID / "orchestrator.ndjson").read_text().splitlines()
                  if '"run_submit"' in ln)
    assert submit["dev_trace"] is True
    assert "dev_trace" not in next(json.loads(ln) for ln in (off / RID / "orchestrator.ndjson").read_text().splitlines()
                                   if '"run_submit"' in ln)


def test_a_dependent_of_a_failed_step_is_skipped_in_the_stream(goals, fake, monkeypatch):
    monkeypatch.setattr(engines, "_plane", lambda: _Plane())
    plan = json.dumps([{"id": "t1", "title": "a", "prompt": "p", "deps": []},
                       {"id": "t2", "title": "b", "prompt": "q", "deps": ["t1"]}])
    fake.chat_replies += [chat_reply(plan), chat_reply("```bash\nfail\n```")]
    run_dir = goals / RID
    run_dir.mkdir(parents=True)
    devtrace.enable(run_dir)
    orch = Orchestrator(run_dir, router=_Router(), place_fn=lambda *a, **k: Placement("cloud-sandbox", "vm"))
    orch.submit("g", engines.decompose_engine("m", api_key="k"), run_id=RID)
    for _ in range(4):
        orch.work(engines.work_fn("m", api_key="k"))
    orch.settle_trace(RID)
    evs = stream(goals)
    assert all(errors(e) == [] for e in evs)
    skipped = next(e["data"] for e in evs if e["type"] == "step" and e["data"]["status"] == "skipped")
    assert skipped["id"] == "step_t2" and "t1" in skipped["detail"]
    assert next(e for e in evs if e["type"] == "error" and e["data"]["code"] == "DependencyFailed")["data"]["step"] == "step_t2"
    assert evs[-1]["type"] == "run" and evs[-1]["data"]["status"] == "failed"


def test_an_unplannable_goal_ends_the_stream_with_a_failed_run(goals, fake):
    run_dir = goals / RID
    run_dir.mkdir(parents=True)
    devtrace.enable(run_dir)
    orch = Orchestrator(run_dir, router=_Router())
    with pytest.raises(Exception):
        orch.submit("g", lambda p: "not json at all", run_id=RID)
    evs = stream(goals)
    assert [e["type"] for e in evs] == ["run", "error", "run"] and evs[-1]["data"]["status"] == "failed"
    assert all(errors(e) == [] for e in evs)


def test_a_requeued_step_stays_running_with_waiting_on_until_it_is_claimed_again(goals, fake, monkeypatch):
    class Down(_Plane):
        up = False

        def sandboxes_available(self):
            return Down.up

    monkeypatch.setattr(engines, "_plane", lambda: Down())
    fake.chat_replies += [chat_reply(json.dumps([{"id": "t1", "title": "a", "prompt": "p"}])),
                          chat_reply("```bash\necho ok\n```")]
    run_dir = goals / RID
    run_dir.mkdir(parents=True)
    devtrace.enable(run_dir)
    orch = Orchestrator(run_dir, router=_Router(), place_fn=lambda *a, **k: Placement("cloud-sandbox", "vm"))
    orch.submit("g", engines.decompose_engine("m", api_key="k"), run_id=RID)
    wfn = engines.work_fn("m", api_key="k")
    orch.work(wfn)  # sandbox control plane down: requeued, not failed
    waiting = [e for e in stream(goals) if e["type"] == "step" and "waiting_on" in e["data"]]
    (w,) = waiting
    assert w["data"]["status"] == "running" and w["data"]["waiting_on"]["kind"] == "sandbox_boot"
    assert (w["data"]["waiting_on"]["attempt"], w["data"]["waiting_on"]["max"]) == (1, 4)
    assert errors(w) == [] and w["data"]["waiting_on"]["since"] > 0
    Down.up = True
    orch.work(wfn)
    last = [e for e in stream(goals) if e["type"] == "step"][-1]["data"]
    assert last["status"] == "done" and "waiting_on" not in last


# ---- through goalruns.submit: the gate is decided once, at submit ---------------------------------------------


def wait_done(rid: str, n: int):
    for _ in range(400):
        got = goalruns.get_run(rid)
        if got and len(got["subtasks"]) == n and all(s["state"] in ("DONE", "FAILED") for s in got["subtasks"]):
            return got
        time.sleep(0.02)
    raise AssertionError("run did not settle")


def submit_two(**kw):
    return goalruns.submit(
        "Do two things",
        lambda prompt: '[{"id":"a","title":"first","prompt":"p1"},{"id":"b","title":"second","prompt":"p2","deps":["a"]}]',
        kw.pop("wfn", lambda task: f"out:{task['prompt']}"), by="test", **kw)


def settled(rid: str, goals: Path) -> list[dict]:
    for _ in range(400):
        evs = stream(goals, rid)
        if evs and evs[-1]["type"] == "run" and evs[-1]["data"]["status"] in ("done", "failed"):
            return evs
        time.sleep(0.02)
    return stream(goals, rid)


def test_submit_with_dev_trace_off_creates_no_stream(goals):
    rid = submit_two()
    wait_done(rid, 2)
    time.sleep(0.1)
    assert not (goals / rid / devtrace.STREAM).exists()
    assert not any("events" in p.name for p in (goals / rid).rglob("*"))


def test_submit_with_the_env_gate_traces_from_the_first_event(goals, monkeypatch):
    monkeypatch.setenv("OC_DEV_TRACE", "1")
    rid = submit_two()
    wait_done(rid, 2)
    evs = settled(rid, goals)
    assert evs[0]["type"] == "run" and evs[0]["data"]["status"] == "running" and evs[-1]["data"]["status"] == "done"
    assert [e["data"]["id"] for e in evs if e["type"] == "step" and e["data"]["status"] == "pending"] == ["step_a", "step_b"]
    assert [e["data"]["id"] for e in evs if e["type"] == "route"] == ["route_a", "route_b"]
    assert all(errors(e) == [] for e in evs)


def test_the_run_keeps_its_stream_when_the_toggle_is_switched_off_mid_run(goals):
    devtrace.write_setting(True)

    def toggling(task):
        devtrace.write_setting(False)  # the owner switches dev trace off while the run is working
        return "out"

    first = submit_two(wfn=toggling)
    wait_done(first, 2)
    evs = settled(first, goals)
    assert evs[-1]["data"]["status"] == "done"  # the run kept tracing to its end
    second = submit_two()
    wait_done(second, 2)
    assert not (goals / second / devtrace.STREAM).exists()  # the next run starts untraced


# ---- the log itself (KTD8) ------------------------------------------------------------------------------------

WRITER = (
    "import sys\n"
    "from oc.events import FileLog\n"
    "log = FileLog(sys.argv[1], 'events.ndjson')\n"
    "for i in range(200):\n"
    "    log.append('goal_0123abcd', 'note', {'id': 'note_' + sys.argv[2] + str(i), 'role': 'agent', 'text': 'x'}, 1.0)\n"
)


def test_two_processes_append_to_one_stream_with_a_gapless_seq(tmp_path):
    procs = [subprocess.Popen([sys.executable, "-c", WRITER, str(tmp_path), tag], cwd=ROOT,
                              env={**os.environ, "PYTHONPATH": str(ROOT)}) for tag in ("a", "b")]
    assert [p.wait(60) for p in procs] == [0, 0]
    evs = FileLog(tmp_path, "events.ndjson").read("goal_0123abcd")
    assert [e["seq"] for e in evs] == list(range(1, 401))
    assert len({e["data"]["id"] for e in evs}) == 400


def test_5000_appends_do_not_reparse_the_file(tmp_path):
    log = FileLog(tmp_path, "events.ndjson")
    t0 = time.monotonic()
    for i in range(5000):
        log.append("goal_0123abcd", "note", {"id": f"note_{i}", "role": "agent", "text": "x" * 80}, 1.0)
    took = time.monotonic() - t0
    assert log.last_seq("goal_0123abcd") == 5000 and took < 10, took


def test_readers_never_create_the_lock_file_or_the_directory(tmp_path):
    log = FileLog(tmp_path / "nope", "events.ndjson")
    assert log.read("goal_0123abcd") == [] and log.last_seq("goal_0123abcd") == 0 and not log.finished("goal_0123abcd")
    assert not (tmp_path / "nope").exists()
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "events.ndjson").write_text("")
    log = FileLog(tmp_path / "run", "events.ndjson")
    log.read("goal_0123abcd"), log.last_seq("goal_0123abcd"), log.finished("goal_0123abcd")
    assert sorted(p.name for p in (tmp_path / "run").iterdir()) == ["events.ndjson"]


def test_an_append_after_a_torn_line_keeps_the_seq_and_the_new_event_readable(tmp_path):
    log = FileLog(tmp_path, "events.ndjson")
    log.append("goal_0123abcd", "note", {"id": "note_1", "role": "agent", "text": "x"}, 1.0)
    with open(tmp_path / "events.ndjson", "a") as f:
        f.write('{"id":"goal_0123abcd:2","v":1,"seq":2')  # crash mid-write, no newline
    again = FileLog(tmp_path, "events.ndjson")
    assert again.last_seq("goal_0123abcd") == 1
    again.append("goal_0123abcd", "note", {"id": "note_2", "role": "agent", "text": "y"}, 2.0)
    assert [e["seq"] for e in FileLog(tmp_path, "events.ndjson").read("goal_0123abcd")] == [1, 2]


def test_last_seq_finds_the_last_line_when_events_are_larger_than_a_read_chunk(tmp_path):
    log = FileLog(tmp_path, "events.ndjson")
    for i in range(3):
        log.append("goal_0123abcd", "note", {"id": f"note_{i}", "role": "agent", "text": "z" * 30_000}, 1.0)
    assert log.last_seq("goal_0123abcd") == 3


def test_per_goal_files_keep_working_as_before(tmp_path):
    log = FileLog(tmp_path)
    log.append("g_one", "run", {"status": "running"}, 1.0)
    log.append("g_two", "run", {"status": "done"}, 1.0)
    assert sorted(p.name for p in tmp_path.glob("*.ndjson")) == ["g_one.ndjson", "g_two.ndjson"]
    assert log.goals() == ["g_one", "g_two"] and log.finished("g_two") and not log.finished("g_one")
