"""The event stream contract: schema, validator, emitter, logs, fixtures."""

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from oc.events import (
    ApprovalStateError,
    Emitter,
    FileLog,
    MemoryLog,
    entities,
    errors,
    fold,
    payload_hash,
    schema,
    validate,
)
from oc.events.log import TERMINAL
from services.gate.gate import Gate
from services.gate.queue import ApprovalQueue, QueueError
from services.gate.queue import payload_hash as queue_payload_hash

FIX = Path(__file__).resolve().parents[2] / "services/workspace/fixtures"
FIXTURE_TYPES = ["run", "step", "tool_call", "source", "artifact", "change", "approval", "cost", "note", "error"]
TYPES = [*FIXTURE_TYPES, "route", "llm_call"]  # route and llm_call are the dev-trace additions (KTD7)


def fixture_events(goal):
    return [json.loads(ln) for ln in (FIX / f"{goal}.ndjson").read_text().splitlines()]


GOALS = sorted(p.name.removesuffix(".ndjson") for p in FIX.glob("g_*.ndjson"))


def test_three_recorded_goals_exist():
    assert GOALS == ["g_cap01", "g_coding01", "g_research01"]


@pytest.mark.parametrize("goal", GOALS)
def test_every_fixture_event_validates(goal):
    for ev in fixture_events(goal):
        assert errors(ev) == [], (goal, ev["seq"], errors(ev))


@pytest.mark.parametrize("goal", GOALS)
def test_ordering_is_gapless_monotonic_and_ids_are_stable(goal):
    evs = fixture_events(goal)
    assert [e["seq"] for e in evs] == list(range(1, len(evs) + 1))
    assert all(a["ts"] <= b["ts"] for a, b in zip(evs, evs[1:], strict=False))
    assert all(e["id"] == f"{goal}:{e['seq']}" and e["goal"] == goal for e in evs)
    assert len({e["id"] for e in evs}) == len(evs)


def test_every_event_type_appears_in_the_fixtures():
    seen = {e["type"] for g in GOALS for e in fixture_events(g)}
    assert seen == set(FIXTURE_TYPES)


def test_schema_lists_exactly_the_twelve_event_types():
    s = schema()
    assert s["$defs"]["event"]["properties"]["type"]["enum"] == TYPES
    assert {a["properties"]["type"]["const"] for a in s["$defs"]["event"]["oneOf"]} == set(TYPES)


def good():
    return fixture_events("g_research01")[0]


@pytest.mark.parametrize(
    "mutate,why",
    [
        (lambda e: e.pop("seq"), "missing seq"),
        (lambda e: e.update(seq=0), "seq below 1"),
        (lambda e: e.update(type="nope"), "unknown type"),
        (lambda e: e.update(extra=1), "unexpected field"),
        (lambda e: e.update(goal="research"), "bad goal id"),
        (lambda e: e["data"].update(status="exploding"), "bad enum"),
        (lambda e: e["data"].update(cap_usd=-1), "negative money"),
        (lambda e: e["data"].update(surprise=True), "unexpected data field"),
        (lambda e: e.update(v=2), "wrong version"),
    ],
)
def test_validator_rejects(mutate, why):
    ev = good()
    mutate(ev)
    assert errors(ev), why


def test_validator_refuses_schema_keywords_it_does_not_understand(monkeypatch):
    import importlib

    v = importlib.import_module("oc.events.validate")
    bad = {"type": "object", "patternProperties": {}}
    with pytest.raises(ValueError, match="does not support"):
        v._check({}, bad, "", [])


def test_approval_needs_a_hash_and_risk():
    ev = [e for e in fixture_events("g_coding01") if e["type"] == "approval"][0]
    del ev["data"]["payload_hash"]
    assert any("payload_hash" in m for m in errors(ev))


# ---- emitter -------------------------------------------------------------------------------------------------


def make(goal="g_t1"):
    t = iter(x / 10 for x in range(10_000, 99_999))
    return Emitter(MemoryLog(), goal, actor={"name": "t"}, clock=lambda: next(t)), None


def test_emitter_assigns_monotonic_seq_and_validates_before_writing():
    em, _ = make()
    em.run("running", title="x", cap_usd=1)
    em.step("step_a", "A", "running")
    with pytest.raises(ValueError):
        em.step("step_a", "A", "teleporting")
    em.note("note_1", "agent", "hi")
    assert [e["seq"] for e in em.log.read("g_t1")] == [1, 2, 3]  # the bad one wrote nothing, no gap


def test_cost_marks_the_80_and_100_percent_crossings_once():
    em, _ = make()
    em.cost(0.5, 1.0)
    a = em.cost(0.81, 1.0)
    b = em.cost(0.9, 1.0)
    c = em.cost(1.0, 1.0)
    d = em.cost(1.0, 1.0)
    assert [x["data"].get("threshold") for x in (a, b, c, d)] == [0.8, None, 1.0, None]


def test_approval_hash_is_computed_not_supplied_and_matches_the_queue():
    em, _ = make()
    payload = {"url": "https://x", "n": 1}
    ev = em.approval_request("ap_1", kind="network", action="GET x", payload=payload, risk="low")
    assert ev["data"]["payload_hash"] == payload_hash(payload) == queue_payload_hash(payload)


def test_an_approval_id_is_single_use_and_a_changed_payload_is_a_new_request():
    em, _ = make()
    em.approval_request("ap_1", kind="push", action="git push", payload={"b": "main"}, risk="high")
    with pytest.raises(ApprovalStateError, match="new request"):
        em.approval_request("ap_1", kind="push", action="git push", payload={"b": "other"}, risk="high")
    em.approval_resolve("ap_1", "approved", decision="once", decided_by="luke")
    with pytest.raises(ApprovalStateError, match="single-use"):
        em.approval_resolve("ap_1", "denied", decision="deny", decided_by="luke")
    em.approval_request("ap_2", kind="push", action="git push", payload={"b": "other"}, risk="high", supersedes="ap_1")
    assert entities(em.log.read("g_t1"), "approval")["ap_2"]["status"] == "pending"


def test_superseding_a_pending_approval_closes_it_in_stream_and_queue(tmp_path):
    q = ApprovalQueue(tmp_path / "q.ndjson", Gate(tmp_path / "a.ndjson"))
    em = Emitter(MemoryLog(), "g_t2", clock=lambda: 1.0, approvals=q)
    em.approval_request("ap_old", kind="push", action="git push", payload={"v": 1}, risk="high")
    em.approval_request("ap_new", kind="push", action="git push", payload={"v": 2}, risk="high", supersedes="ap_old")
    assert entities(em.log.read("g_t2"), "approval")["ap_old"]["status"] == "superseded"
    assert q.get("ap_old")["status"] == "superseded"
    with pytest.raises(QueueError) as e:
        q.decide("ap_old", approve=True, by="luke", expect_hash=q.get("ap_old")["payload_hash"])
    assert e.value.status == 409
    # the new request is bound to its own hash: the old hash can't approve it
    with pytest.raises(QueueError):
        q.decide("ap_new", approve=True, by="luke", expect_hash=payload_hash({"v": 1}))
    assert q.decide("ap_new", approve=True, by="luke", expect_hash=payload_hash({"v": 2}))["allowed"]


def test_resolve_unknown_or_to_pending_is_refused():
    em, _ = make()
    with pytest.raises(ApprovalStateError):
        em.approval_resolve("ap_nope", "approved")
    em.approval_request("ap_1", kind="email", action="send", payload="body", risk="low")
    with pytest.raises(ApprovalStateError):
        em.approval_resolve("ap_1", "pending")


def test_file_log_persists_resumes_and_tolerates_a_torn_line(tmp_path):
    log = FileLog(tmp_path)
    em = Emitter(log, "g_f1", clock=lambda: 5.0)
    em.run("running", title="t")
    em.note("note_1", "user", "hello")
    with open(tmp_path / "g_f1.ndjson", "a") as f:
        f.write('{"id":"g_f1:3","v":1,"goal":"g_f1","seq":3')  # crash mid-write
    again = FileLog(tmp_path)
    assert [e["seq"] for e in again.read("g_f1")] == [1, 2]
    assert [e["seq"] for e in again.read("g_f1", after=1)] == [2]
    assert again.goals() == ["g_f1"]


# ---- dev trace stream (KTD7) ---------------------------------------------------------------------------------

DEV_GOAL = "goal_0123abcd"


def dev_stream() -> list[dict]:
    """A hand-built dev run: one of every new shape, in the order a real run writes them."""
    data = [
        ("run", {"status": "running", "title": "Two things", "goal_text": "Do two things"}),
        ("llm_call", {"id": "call_plan", "kind": "plan", "status": "ok", "detail": "[{\"id\":\"t1\"}]"}),
        ("step", {"id": "step_t1", "title": "first", "status": "pending"}),
        ("route", {"id": "route_t1", "step": "step_t1", "route": "cloud", "tier": "cheap", "engine": "jev",
                   "model": "vendor/model", "confidence": 0.9, "cost_usd": 0.001, "ms": 12,
                   "placement": "cloud-sandbox",
                   "fallbacks": [{"engine": "decider", "kind": "EngineDown", "status": 404, "reason": "no endpoints"}]}),
        ("step", {"id": "step_t1", "title": "first", "status": "running"}),
        ("llm_call", {"id": "call_0123456789abcdef", "kind": "work", "status": "ok", "step": "step_t1",
                      "model_requested": "vendor/model", "model_served": "vendor/model-2", "provider": "Acme",
                      "tokens_in": 10, "tokens_out": 5, "tokens_cached": 2, "tokens_reasoning": 1,
                      "cost_usd": 0.0012, "latency_ms": 840, "reasoning": "because"}),
        ("tool_call", {"id": "tool_task_1_1", "name": "shell.exec", "status": "error", "step": "step_t1",
                       "command": "exit 1", "rc": 1, "stdout_tail": [], "stderr_tail": ["boom"], "duration_ms": 31}),
        ("step", {"id": "step_t1", "title": "first", "status": "running",
                  "waiting_on": {"kind": "sandbox_boot", "attempt": 2, "max": 4, "since": 5.5, "detail": "refused"},
                  "model": "vendor/model-2"}),
        ("step", {"id": "step_t1", "title": "first", "status": "failed"}),
        ("error", {"id": "err_task_1", "code": "RuntimeError", "message": "sandbox exec rc=1: boom", "step": "step_t1"}),
        ("run", {"status": "waiting", "reason": "sandbox"}),
        ("run", {"status": "waiting", "reason": "queue"}),
        ("run", {"status": "failed", "reason": "error"}),
    ]
    return [
        {"id": f"{DEV_GOAL}:{i}", "v": 1, "goal": DEV_GOAL, "seq": i, "ts": 100.0 + i, "type": t, "data": d}
        for i, (t, d) in enumerate(data, 1)
    ]


def test_a_dev_run_stream_validates_with_one_of_each_new_type():
    evs = dev_stream()
    assert {"route", "llm_call"} <= {e["type"] for e in evs}
    for e in evs:
        assert errors(e) == [], (e["seq"], errors(e))


@pytest.mark.parametrize(
    "mutate,why",
    [
        (lambda e: e["data"].update(id="Step T1"), "entity id must match the ref pattern"),
        (lambda e: e["data"].update(surprise=1), "unknown data field"),
        (lambda e: e["data"].update(status="exploding"), "bad status"),
    ],
)
def test_dev_stream_shapes_reject_bad_data(mutate, why):
    for typ in ("route", "llm_call", "tool_call"):
        ev = next(e for e in dev_stream() if e["type"] == typ)
        mutate(ev)
        assert errors(ev), (typ, why)


def test_tool_call_keeps_rejecting_unknown_fields_but_accepts_command_fields():
    ev = next(e for e in dev_stream() if e["type"] == "tool_call")
    assert errors(ev) == []
    ev["data"]["stdout_tail"] = ["x"] * 51
    assert errors(ev)


def test_the_old_goal_id_pattern_still_applies_and_goal_hex_ids_are_exact():
    ev = dev_stream()[0]
    assert ev["id"] == "goal_0123abcd:1" and errors(ev) == []
    for bad_goal in ("goal_0123ABCD", "goal_0123abc", "goal_0123abcdef"):
        assert errors({**ev, "goal": bad_goal, "id": f"{bad_goal}:1"})
    assert errors({**ev, "goal": "g_research01", "id": "g_research01:1"}) == []


def test_waiting_on_clears_when_the_next_step_event_carries_none():
    evs = [e for e in dev_stream() if e["type"] == "step"]
    seen = [entities(evs[: i + 1], "step")["step_t1"].get("waiting_on") for i in range(len(evs))]
    assert seen[1] is None and seen[2]["attempt"] == 2 and seen[3] is None


def test_fold_accepts_the_dev_run_reasons():
    runs = [e for e in dev_stream() if e["type"] == "run"]
    assert (fold(runs[:2])["status"], fold(runs[:2])["reason"]) == ("waiting", "sandbox")
    assert fold(runs[:3])["reason"] == "queue"


def test_concurrent_emitters_never_gap_or_duplicate_seq(tmp_path):
    log = FileLog(tmp_path)

    def work(k):
        em = Emitter(log, "g_c1", clock=lambda: 1.0)
        for i in range(40):
            em.note(f"note_{k}_{i}", "agent", "x")

    ts = [threading.Thread(target=work, args=(k,)) for k in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert [e["seq"] for e in log.read("g_c1")] == list(range(1, 241))


def test_readers_wake_when_an_event_arrives():
    log = MemoryLog()
    em = Emitter(log, "g_w1", clock=lambda: 1.0)
    got = []
    t = threading.Thread(target=lambda: got.append(log.wait("g_w1", 0, 5)))
    t.start()
    em.run("running", title="x")
    t.join(5)
    assert got == [True]
    assert log.wait("g_w1", 1, 0.05) is False


def test_finished_means_a_terminal_run_status():
    em, _ = make()
    assert not em.log.finished("g_t1")
    em.run("paused", reason="cap")
    assert not em.log.finished("g_t1")
    em.run("done")
    assert em.log.finished("g_t1") and "done" in TERMINAL


def test_fold_groups_goals_for_the_left_column():
    cap = fold(fixture_events("g_cap01"))
    assert (cap["status"], cap["reason"], cap["group"]) == ("done", None, "done")
    paused = fold([e for e in fixture_events("g_cap01") if e["seq"] <= 32])
    assert paused["status"] == "paused" and paused["reason"] == "cap" and paused["group"] == "needs_you"
    held = fold([e for e in fixture_events("g_coding01") if e["seq"] <= 12])
    assert held["pending_approvals"] == 1 and held["group"] == "needs_you"


def test_fixtures_regenerate_byte_for_byte(tmp_path):
    env = {**os.environ, "OC_FIXTURE_OUT": str(tmp_path)}
    subprocess.run([sys.executable, "-I", str(FIX / "build.py")], check=True, env=env, capture_output=True)
    for f in sorted(FIX.rglob("*")):
        if f.is_file() and f.name != "build.py" and "__pycache__" not in f.parts:
            assert (tmp_path / f.relative_to(FIX)).read_bytes() == f.read_bytes(), f"{f.name} drifted: re-run build.py"


def test_event_doc_and_schema_agree():
    doc = (Path(__file__).resolve().parents[2] / "docs/design/event-stream.md").read_text()
    for t in TYPES:
        assert f"`{t}`" in doc
    assert "Last-Event-ID" in doc and "event.schema.json" in doc
    json.loads((Path(__file__).resolve().parents[2] / "oc/events/schema/event.schema.json").read_text())
    assert validate(good())
