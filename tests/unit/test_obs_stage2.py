"""Stage 2: every service speaks the shared log dialect (`oc.obs`): ids, levels, metadata only, redacted.

No test here reaches a real provider: the conftest strips ambient keys and a guard fails any call to
openrouter.ai; the end-to-end runs use `fake_openrouter`.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from fake_cube import FakeCubeSDK
from fake_openrouter import FakeOpenRouter, chat_reply
from oc.obs import context, get_logger
from oc.obs import log as obs_log
from services import telemetry
from services.cost import CostLedger, Usage
from services.device import DeviceRegistry, GrantStore, mint_secret, seal
from services.device.protocol import EnvelopeError, open_envelope
from services.device_agent import execute
from services.device_agent.heartbeat import registration_payload
from services.execplane.client import ExecplaneAdapter
from services.execplane.policy import PolicyDenied
from services.gate import Gate
from services.gate.queue import ApprovalQueue
from services.mcp.server import State, handle
from services.orchestrator import Orchestrator, engines
from services.placement import Placement
from services.router import Budget, Router

PLAN = json.dumps([
    {"id": "a", "title": "A", "prompt": "do a", "deps": []},
    {"id": "b", "title": "B", "prompt": "do b", "deps": ["a"]},
])


@pytest.fixture
def logs(capsys):
    """Parsed JSON log rows written since the last call."""
    obs_log.configure(force=True)

    def read(service: str | None = None, msg: str | None = None) -> list[dict]:
        rows = [json.loads(ln) for ln in capsys.readouterr().err.splitlines() if ln.startswith("{")]
        return [r for r in rows if (service is None or r["service"] == service) and (msg is None or r["msg"] == msg)]

    return read


@pytest.fixture
def fake(monkeypatch):
    with FakeOpenRouter() as f:
        monkeypatch.setenv("OC_OPENROUTER_BASE_URL", f.url)
        yield f


def cloud(*a, **k):
    return Placement("cloud-OpenRouter", "test")


def run_goal(tmp_path, fake, goal="write two things", *, work=None, chat_id=None):
    fake.chat_replies += [chat_reply(PLAN, cost=0.001), chat_reply("A-done", cost=0.004), chat_reply("B-done", cost=0.006)]
    led = CostLedger(tmp_path / "cost.ndjson", default_cap=1.0)
    orch = Orchestrator(tmp_path / "o", ledger=led, place_fn=cloud)
    run_id = orch.submit(goal, engines.decompose_engine(api_key="k"), chat_id=chat_id)
    fn = work or engines.work_fn(api_key="k")
    orch.work(fn)
    orch.work(fn)
    return orch, led, run_id


# --- end to end ----------------------------------------------------------------------------------


def test_a_full_run_logs_from_every_layer_on_one_trace(tmp_path, fake, monkeypatch):
    monkeypatch.setenv("OC_DEBUG", "1")
    monkeypatch.setenv("OC_LOG_DIR", str(tmp_path / "logs"))
    obs_log.configure(force=True)
    _, _, run_id = run_goal(tmp_path, fake, chat_id="chat-1")
    rows = [json.loads(ln) for ln in (tmp_path / "logs" / "oc.log").read_text().splitlines()]
    services = {r["service"] for r in rows}
    assert {"orchestrator", "router", "placement", "gate", "worker", "cost", "openrouter"} <= services
    mine = [r for r in rows if r.get("run_id") == run_id]
    assert mine and all(r["trace_id"] == context.trace_id_for(run_id) and r["chat_id"] == "chat-1" for r in mine)
    msgs = {r["msg"] for r in rows}
    assert {"run submitted", "route decided", "placement decided", "subtask dispatched", "task claimed",
            "task done", "cost recorded"} <= msgs
    worker = [r for r in rows if r["msg"] == "task done"]
    assert {r["subtask"] for r in worker} == {"a", "b"} and all(isinstance(r["ms"], float) for r in worker)


def test_logs_hold_call_metadata_only_unless_content_capture_is_on(tmp_path, fake, monkeypatch, capsys):
    monkeypatch.setenv("OC_DEBUG", "1")
    obs_log.configure(force=True)
    goal = "quarterly-plan-private-words"
    run_goal(tmp_path, fake, goal=goal)
    err = capsys.readouterr().err
    assert "run submitted" in err and goal not in err and "do a" not in err and "A-done" not in err
    assert '"goal_chars":' in err


def test_seeded_secrets_never_reach_a_log_or_the_call_record(tmp_path, fake, monkeypatch, capsys):
    secrets = ["sk-or-v1-0123456789abcdef0123456789abcdef", "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
               "tally-agent-key-0123456789", "bob@example.com"]
    monkeypatch.setenv("OC_TALLYLINE_KEY", secrets[2])
    monkeypatch.setenv("OC_DEBUG", "1")
    monkeypatch.setenv("OC_LOG_DIR", str(tmp_path / "logs"))
    obs_log.configure(force=True)

    def boom(task):
        if task["subtask"] == "b":
            raise RuntimeError("provider said: " + " ".join(secrets))
        return "ok"

    goal = "use " + " and ".join(secrets)
    run_goal(tmp_path, fake, goal=goal, work=boom)
    texts = [capsys.readouterr().err, (tmp_path / "logs" / "oc.log").read_text(),
             (Path(tmp_path / "obs") / "openrouter-calls.ndjson").read_text()]
    for t in texts:
        assert t.strip()
        for s in secrets:
            assert s not in t


def test_error_text_is_logged_only_with_content_capture(tmp_path, fake, monkeypatch, logs):
    def boom(task):
        raise RuntimeError("private failure detail")

    run_goal(tmp_path, fake, work=boom)
    (row, *_) = logs("worker", "task failed")
    assert row["error"] == "RuntimeError" and "error_message" not in row and "private failure detail" not in json.dumps(row)
    monkeypatch.setenv("OC_OBS_CAPTURE_CONTENT", "1")
    fake.chat_replies.clear()
    run_goal(tmp_path / "2", fake, work=boom)
    (row, *_) = logs("worker", "task failed")
    assert row["error_message"] == "private failure detail"


def test_a_broken_log_sink_does_not_fail_a_run(tmp_path, fake, monkeypatch):
    monkeypatch.setenv("OC_LOG_DIR", "/proc/not/writable")
    orch, _, run_id = run_goal(tmp_path, fake)
    assert all(v["state"] == "DONE" for v in orch.results(run_id).values())


def test_recover_and_dep_failure_are_logged(tmp_path, fake, logs):
    fake.chat_replies.append(chat_reply(PLAN))
    orch = Orchestrator(tmp_path / "o", place_fn=cloud)
    run_id = orch.submit("g", engines.decompose_engine(api_key="k"))
    logs()
    fresh = Orchestrator(tmp_path / "o", place_fn=cloud)
    assert fresh.recover(run_id)
    (rec,) = logs("orchestrator", "run recovered")
    assert rec["run_id"] == run_id and rec["subtasks"] == 2 and rec["trace_id"] == context.trace_id_for(run_id)
    tid = fresh.dispatches[run_id][0]["task"]
    fresh.fs.fail(tid)
    fresh.dispatch_ready(run_id)
    (dep,) = logs("orchestrator", "dep failed: tombstoned dependent")
    assert dep["subtask"] == "b" and dep["blocked_by"] == "a" and dep["level"] == "WARNING"


# --- router / placement --------------------------------------------------------------------------


def test_router_logs_the_decision_and_every_fallback(tmp_path, logs):
    def down(utterance):
        raise ConnectionError("engine offline")

    down.__name__ = "decider"
    router = Router(Budget(tmp_path / "ledger.jsonl"), [down])
    d = router.route("summarise this private document text")
    assert d.engine == "table"
    (warn,) = logs("router", "engine down, trying next")
    assert warn["level"] == "WARNING" and warn["engine"] == "decider" and warn["error"] == "ConnectionError"
    logs()  # drained above for the first call; run again to read both lines together
    router.route("another private utterance")
    rows = logs("router")
    assert [r["msg"] for r in rows] == ["engine down, trying next", "all engines down: table fallback", "route decided"]
    decided = rows[-1]
    assert {"route", "tier", "engine", "confidence", "cost", "ms"} <= set(decided) and decided["engine"] == "table"
    assert "private" not in json.dumps(rows)


def test_budget_halt_is_a_warning(tmp_path, logs):
    b = Budget(tmp_path / "l.jsonl", daily_cap=1.0, task_cap=0.01)
    from services.router.budget import BudgetHalt

    with pytest.raises(BudgetHalt):
        b.check(0.5)
    (row,) = logs("router", "budget halt")
    assert row["level"] == "WARNING" and row["cap"] == "task"


def test_blocked_placement_is_a_placement_warning_with_the_reason(tmp_path, fake, logs):
    fake.chat_replies.append(chat_reply(PLAN))
    orch = Orchestrator(tmp_path / "o", place_fn=lambda *a, **k: Placement("blocked", "no legal home right now"))
    orch.submit("g", engines.decompose_engine(api_key="k"))
    (row, *_) = logs("placement", "placement blocked")
    assert row["level"] == "WARNING" and row["reason"] == "no legal home right now"


def test_placement_inputs_appear_only_at_debug(tmp_path, fake, logs, monkeypatch):
    fake.chat_replies.append(chat_reply(PLAN))
    Orchestrator(tmp_path / "o", place_fn=cloud).submit("g", engines.decompose_engine(api_key="k"))
    assert logs("placement", "placement inputs") == []
    monkeypatch.setenv("OC_LOG_LEVEL_PLACEMENT", "DEBUG")
    fake.chat_replies.append(chat_reply(PLAN))
    Orchestrator(tmp_path / "o2", place_fn=cloud).submit("g", engines.decompose_engine(api_key="k"))
    assert logs("placement", "placement inputs")


# --- gate ----------------------------------------------------------------------------------------


def test_gate_logs_categories_and_decisions_never_the_scanned_text(tmp_path, monkeypatch, logs):
    monkeypatch.setenv("OC_DEBUG", "1")
    gate = Gate(tmp_path / "audit.ndjson")
    gate.scan("my ssn is 123-45-6789 and card 4111 1111 1111 1111")
    (scan,) = logs("gate", "scan")
    assert "ssn" in scan["categories"] and "123-45-6789" not in json.dumps(scan)

    payload = {"model": "m/x", "messages": [{"content": "private body"}]}
    ap = gate.grant(payload, by="owner")
    assert gate.request_cloud_call(payload, ap, sender=lambda p: 12).allowed
    replay = gate.request_cloud_call(payload, ap, sender=lambda p: 12)
    assert not replay.allowed and replay.reason == "replayed"
    rows = logs("gate")
    by = {r["msg"]: r for r in rows}
    assert by["approval granted"]["approval_id"] == ap.id and by["cloud call allowed"]["payload_bytes"] > 0
    assert by["cloud call blocked"]["level"] == "WARNING" and by["cloud call blocked"]["reason"] == "replayed"
    assert "private body" not in json.dumps(rows)


def test_approval_queue_lifecycle_is_logged(tmp_path, logs):
    gate = Gate(tmp_path / "a.ndjson")
    q = ApprovalQueue(tmp_path / "q.ndjson", gate)
    iid = q.submit(who="agent", what="send", payload={"model": "m/x", "egress": "api.example.test", "x": "secret-ish"})
    q.decide(iid, approve=False, by="owner", note="no")
    msgs = [(r["msg"], r.get("decision")) for r in logs("gate")]
    assert ("approval requested", None) in msgs and ("approval decided", "sent_back") in msgs


# --- cost ----------------------------------------------------------------------------------------


def test_cost_ledger_logs_spend_pause_and_resume_with_the_run(tmp_path, logs):
    led = CostLedger(tmp_path / "c.ndjson", default_cap=0.01)
    tok = led.check("r1", 0.001)
    led.record("r1", "s", Usage("m/x", 10, 5, 0.02), token=tok)
    led.resume("r1", 0.5, by="owner")
    rows = {r["msg"]: r for r in logs("cost")}
    assert rows["cost recorded"]["run_id"] == "r1" and rows["cost recorded"]["cost"] == 0.02
    assert rows["run paused: cap"]["level"] == "WARNING" and rows["run paused: cap"]["cap"] == 0.01
    assert rows["run resumed"]["by"] == "owner"
    assert "reservation" not in json.dumps(rows["cost recorded"])  # reservation ids are debug-only


def test_ledger_reservation_ids_are_not_masked_as_tokens(tmp_path, logs, monkeypatch):
    monkeypatch.setenv("OC_DEBUG", "1")
    led = CostLedger(tmp_path / "c.ndjson", default_cap=1.0)
    led.release("r", led.check("r", 0.1))
    reserved = logs("cost", "reserved")[0]
    assert reserved["reservation"] != "[REDACTED]" and reserved["run_id"] == "r"


# --- device --------------------------------------------------------------------------------------


class Clock:
    t = 1_700_000_000.0

    def __call__(self):
        return self.t


def test_device_registry_logs_register_heartbeat_gap_and_stale(tmp_path, logs, monkeypatch):
    clk = Clock()
    reg = DeviceRegistry(tmp_path, clock=clk)
    reg.register(registration_payload("mac-mini", ["/private/home/path"], ["fs"]))
    clk.t += 10
    reg.heartbeat("mac-mini")
    clk.t += 200
    reg.eligible()
    reg.heartbeat("mac-mini")
    rows = logs("device")
    assert "heartbeat" not in {r["msg"] for r in rows}  # routine heartbeats are DEBUG
    msgs = [r["msg"] for r in rows]
    assert "device registered" in msgs and "heartbeat gap" in msgs and "device went stale: marked offline" in msgs
    assert next(r for r in rows if r["msg"] == "heartbeat gap")["level"] == "WARNING"
    assert "/private/home/path" not in json.dumps(rows)  # roots are counted, not printed


def test_envelope_refusals_log_the_reason_never_signature_nonce_or_secret(logs):
    secret = mint_secret()
    env = seal({"kind": "echo", "text": "private text", "device_id": "d1"}, secret)
    assert open_envelope(env, secret)["kind"] == "echo"
    bad = {**env, "sig": "0" * 64}
    with pytest.raises(EnvelopeError):
        open_envelope(bad, secret)
    seen: set[str] = set()
    open_envelope(env, secret, seen=seen)
    with pytest.raises(EnvelopeError):
        open_envelope(env, secret, seen=seen)
    rows = logs("device")
    refused = [r for r in rows if r["msg"] == "envelope refused"]
    assert [r["reason"] for r in refused] == ["bad signature", "replayed nonce"] and refused[0]["level"] == "WARNING"
    raw = json.dumps(rows)
    for leak in (secret, env["sig"], env["nonce"], "private text"):
        assert leak not in raw


def test_grants_log_ids_and_verdicts_never_paths(tmp_path, logs, monkeypatch):
    monkeypatch.setenv("OC_DEBUG", "1")
    root = tmp_path / "granted-root-xyz"
    root.mkdir()
    store = GrantStore(tmp_path / "devs")
    g = store.add("d1", root, ["read"])
    assert store.check("d1", root / "a.txt", "read").allowed
    assert not store.check("d1", "/etc/outside-xyz/passwd", "read").allowed
    rows = logs("device")
    assert [r["msg"] for r in rows if r["msg"].startswith("grant")] == ["grant added", "grant used", "grant check denied"]
    denied = rows[-1]
    assert denied["level"] == "WARNING" and denied["grant_id"] is None or denied["grant_id"] == g.grant_id
    raw = json.dumps(rows)
    assert "granted-root-xyz" not in raw and "outside-xyz" not in raw


def test_agent_step_log_has_kind_verdict_and_ms_not_the_step_text(logs):
    secret = mint_secret()
    out = execute(seal({"kind": "echo", "text": "private echo text"}, secret), secret)
    assert out["ok"]
    bad = execute({"step": {"kind": "echo"}, "ts": 0, "nonce": "n", "sig": "x"}, secret)
    assert not bad["ok"]
    rows = logs("device_agent")
    assert [(r["msg"], r["ok"]) for r in rows] == [("step executed", True), ("step not executed", False)]
    assert rows[0]["kind"] == "echo" and isinstance(rows[0]["ms"], float) and rows[1]["level"] == "WARNING"
    assert "private echo text" not in json.dumps(rows)


def test_relay_access_log_and_auth_reject(tmp_path, logs, monkeypatch):
    import http.client

    from services.device.security import generate_totp, mint_totp_secret, pair_device
    from services.device_relay import make_server

    root = tmp_path / "oc"
    ts = mint_totp_secret()
    _, tok = pair_device(root, {"device_id": "rpi", "os": "linux", "roots": [], "capabilities": ["fs"]},
                         totp_secret=ts, totp_code=generate_totp(ts))
    srv = make_server(0, root)
    threading.Thread(target=lambda: srv.serve_forever(poll_interval=0.01), daemon=True).start()
    try:
        def call(method, path, bearer=None):
            c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
            c.request(method, path, headers={"Authorization": f"Bearer {bearer}"} if bearer else {})
            r = c.getresponse()
            r.read()
            c.close()
            return r.status

        monkeypatch.setenv("OC_DEBUG", "1")
        logs()
        assert call("POST", "/relay/devices/rpi/heartbeat") == 401
        assert call("POST", "/relay/devices/rpi/heartbeat", tok.secret) == 200
        assert call("GET", "/relay/devices/rpi/nope", tok.secret) == 404
        rows = []
        for _ in range(150):  # the access line is written just after the response; poll instead of racing it
            rows += logs("device_relay", "relay request")
            if len(rows) >= 3:
                break
            time.sleep(0.02)
    finally:
        srv.shutdown()
    assert [(r["op"], r["status"], r["level"]) for r in rows] == [
        ("heartbeat", 401, "WARNING"), ("heartbeat", 200, "DEBUG"), ("unrouted", 404, "WARNING")]
    assert all(r["device_id"] in ("rpi", None) and isinstance(r["ms"], float) for r in rows)
    assert tok.secret not in json.dumps(rows)


# --- mcp -----------------------------------------------------------------------------------------


@pytest.fixture
def mcp_state(tmp_path, monkeypatch):
    for k, f in (("OC_AUDIT", "audit.ndjson"), ("OC_APPROVALS", "approvals.ndjson"),
                 ("OC_COST_LEDGER", "cost.ndjson"), ("OC_ORCH_AUDIT", "orch.ndjson")):
        monkeypatch.setenv(k, str(tmp_path / f))
    return State()


def _mcp(st, name, **args):
    return handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}, st)


def test_mcp_tool_calls_log_name_args_shape_status_and_latency(mcp_state, logs, monkeypatch):
    _mcp(mcp_state, "request_approval", what="send the private report", model="m/x", payload={"k": "private-value"})
    _mcp(mcp_state, "audit_tail", n=3)
    _mcp(mcp_state, "no_such_tool")
    rows = logs("mcp")
    assert [(r["tool"], r["status"]) for r in rows] == [("request_approval", "ok"), ("audit_tail", "ok")]
    assert rows[0]["args_shape"] == {"what": "str", "model": "str", "payload": "dict"} and isinstance(rows[0]["ms"], float)
    assert "private" not in json.dumps(rows)
    monkeypatch.setenv("OC_DEBUG", "1")
    monkeypatch.setenv("OC_OBS_CAPTURE_CONTENT", "1")
    _mcp(mcp_state, "audit_tail", n=3)
    assert any(r["msg"] == "tool call args" and r["args"] == {"n": 3} for r in logs("mcp"))


def test_mcp_missing_argument_is_a_warning(mcp_state, logs):
    _mcp(mcp_state, "approval_status")
    (row,) = logs("mcp")
    assert row["level"] == "WARNING" and row["status"] == "missing_argument"


# --- telemetry -----------------------------------------------------------------------------------


def test_telemetry_failures_are_logged_without_urls_or_keys(monkeypatch, logs):
    monkeypatch.setenv("OC_TALLYLINE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("OC_TALLYLINE_WORKSPACE", "ws-name")
    monkeypatch.setenv("OC_TALLYLINE_KEY", "tally-agent-key-0123456789")
    monkeypatch.setenv("OC_SENTRY_DSN", "https://pubkey123@err.invalid/7")
    telemetry.report_failure("something broke", fingerprint="fp")
    rows = logs("telemetry", "telemetry send failed")
    assert {r["sink"] for r in rows} == {"sentry", "tallyline"} and all(r["level"] == "WARNING" for r in rows)
    raw = json.dumps(rows)
    for leak in ("tally-agent-key-0123456789", "pubkey123", "127.0.0.1:1", "err.invalid", "ws-name"):
        assert leak not in raw


def test_telemetry_with_no_sinks_logs_nothing_at_info(logs):
    telemetry.report_failure("nobody listening")
    assert logs("telemetry") == []


# --- execplane -----------------------------------------------------------------------------------


def test_execplane_logs_sandbox_lifecycle_without_commands_or_output(tmp_path, logs):
    a = ExecplaneAdapter(tmp_path)
    a._sdk = FakeCubeSDK()
    a._api_url = "http://127.0.0.1:9"
    a.policy.apply(a.policy.propose({"add_egress": ["api.example.test"]}, origin="human"), approved_by="human")
    sb = a.make_sandbox(egress_allowlist=["api.example.test"])
    sb.exec("echo private-command-text")
    sb.close()
    rows = logs("execplane")
    msgs = [r["msg"] for r in rows]
    assert msgs[:1] == ["policy change applied"]
    assert {"sandbox create attempt", "sandbox create created", "sandbox exec"} <= set(msgs)
    ex = next(r for r in rows if r["msg"] == "sandbox exec")
    assert ex["cmd_chars"] == len("echo private-command-text") and ex["returncode"] == 0
    assert "private-command-text" not in json.dumps(rows)


def test_unapproved_egress_refusal_is_a_warning_with_the_host(tmp_path, logs):
    a = ExecplaneAdapter(tmp_path)
    a._sdk = FakeCubeSDK()
    with pytest.raises(PolicyDenied):
        a.make_sandbox(egress_allowlist=["evil.example.test"])
    (row,) = logs("execplane", "sandbox create refused")
    assert row["level"] == "WARNING" and row["egress_hosts"] == ["evil.example.test"]


def test_agent_policy_change_refusal_is_logged(tmp_path, logs):
    a = ExecplaneAdapter(tmp_path)
    prop = a.policy.propose({"add_egress": ["x.example.test"]}, origin="agent")
    with pytest.raises(PolicyDenied):
        a.policy.apply(prop, approved_by="agent")
    (row,) = logs("execplane", "policy change refused: not human-approved")
    assert row["level"] == "WARNING"


def test_obs_dialect_loggers_use_the_service_names_in_the_plan():
    names = {get_logger(n).service for n in ("orchestrator", "router", "placement", "gate", "cost", "execplane",
                                             "device", "mcp", "opsview", "telemetry")}
    assert len(names) == 10


def test_orchestrator_run_logs_how_the_run_settled(tmp_path, fake, logs):
    fake.chat_replies += [chat_reply(PLAN), chat_reply("A"), chat_reply("B")]
    orch = Orchestrator(tmp_path / "o", place_fn=cloud)
    orch.run("g", engines.decompose_engine(api_key="k"), engines.work_fn(api_key="k"))
    (row,) = logs("orchestrator", "run settled")
    assert row["states"] == {"DONE": 2} and row["level"] == "INFO"


def test_the_suite_guard_recognises_the_real_provider_and_lets_loopback_through(_no_real_openrouter):
    is_real_provider = _no_real_openrouter
    assert is_real_provider("https://" + "openrouter" + ".ai/api/v1/chat/completions")
    assert not is_real_provider("http://127.0.0.1:41234/chat/completions")
