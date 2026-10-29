"""Step claim time and model reasoning are recorded by the shared worker sweep (inline drive and Temporal alike).

Model calls go to the in-process fake OpenRouter; nothing reaches a real provider.
"""

import json

import pytest

from oc.fsproto import AlreadyLeased
from oc.fsproto import adapter as fs_adapter
from services.gate.audit import AuditLog
from services.openrouter import chat
from services.orchestrator import Orchestrator, run_once
from services.orchestrator.thinking import CAP, ThinkingLog

from fake_openrouter import FakeOpenRouter, chat_reply


@pytest.fixture
def fake(monkeypatch):
    with FakeOpenRouter() as f:
        monkeypatch.setenv("OC_OPENROUTER_BASE_URL", f.url)
        yield f


@pytest.fixture
def sweep(tmp_path):
    fs = fs_adapter(tmp_path / "run")
    audit = AuditLog(tmp_path / "orchestrator.ndjson")
    thinking = ThinkingLog(tmp_path / "thinking.ndjson")

    def send(subtask="s1"):
        return fs.send("worker", {"run": "r1", "subtask": subtask, "title": "t", "prompt": "p"})

    return fs, audit, thinking, send


def _calls(n):
    def work(task):
        return " ".join(chat("m", task["prompt"], key="k").text for _ in range(n))

    return work


def test_claim_writes_worker_start_before_done_and_records_thinking(fake, sweep):
    fs, audit, thinking, send = sweep
    tid = send()
    fake.chat_replies.append(chat_reply("answer", reasoning="because"))
    run_once(fs, audit, "worker", _calls(1), thinking=thinking)
    events = [(r["event"], r.get("task")) for r in audit.rows()]
    assert events == [("worker_start", tid), ("worker_done", tid)]
    start = audit.rows()[0]
    assert start["run"] == "r1" and start["subtask"] == "s1" and start["ts"] <= audit.rows()[1]["ts"]
    (row,) = thinking.rows()
    assert (row["run"], row["task"], row["subtask"], row["text"]) == ("r1", tid, "s1", "because")


def test_failed_step_keeps_its_thinking(fake, sweep):
    fs, audit, thinking, send = sweep
    tid = send()
    fake.chat_replies.append(chat_reply("x", reasoning="tried hard"))

    def work(task):
        chat("m", "p", key="k")
        raise RuntimeError("boom")

    run_once(fs, audit, "worker", work, thinking=thinking)
    assert [r["event"] for r in audit.rows()] == ["worker_start", "worker_fail"]
    assert [r["text"] for r in thinking.rows()] == ["tried hard"]
    assert fs.done(tid) is False


def test_no_reasoning_writes_no_row(fake, sweep):
    fs, audit, thinking, send = sweep
    send()
    fake.chat_replies.append(chat_reply("x"))
    run_once(fs, audit, "worker", _calls(1), thinking=thinking)
    assert thinking.rows() == []


def test_two_calls_in_one_step_make_one_row_in_order(fake, sweep):
    fs, audit, thinking, send = sweep
    send()
    fake.chat_replies += [chat_reply("a", reasoning="first"), chat_reply("b", reasoning="second")]
    run_once(fs, audit, "worker", _calls(2), thinking=thinking)
    (row,) = thinking.rows()
    assert row["text"] == "first\n\nsecond"


def test_long_reasoning_is_capped_and_secrets_redacted(tmp_path):
    log = ThinkingLog(tmp_path / "thinking.ndjson")
    log.append("r1", "t1", "s1", "key sk-or-v1-abcdefabcdefabcdef12 " + "x" * 50_000)
    (row,) = log.rows()
    assert len(row["text"]) <= CAP + 40 and row["text"].endswith("[truncated]")
    assert "sk-or-v1-abcdef" not in row["text"]


def test_unwritable_thinking_log_does_not_change_the_outcome(fake, sweep, tmp_path):
    fs, audit, _, send = sweep
    tid = send()
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    broken = ThinkingLog(blocked)  # a directory: every append fails
    fake.chat_replies.append(chat_reply("ok", reasoning="r"))
    run_once(fs, audit, "worker", _calls(1), thinking=broken)
    assert fs.done(tid)
    assert [r["event"] for r in audit.rows()] == ["worker_start", "worker_done"]


def test_lost_claim_writes_no_worker_start(sweep, monkeypatch):
    fs, audit, thinking, send = sweep
    send()

    def lost(*a, **k):
        raise AlreadyLeased("taken")

    monkeypatch.setattr(fs, "claim", lost)
    assert run_once(fs, audit, "worker", lambda t: "x", thinking=thinking) == []
    assert audit.rows() == []


def test_garbage_lines_in_thinking_log_are_skipped(tmp_path):
    p = tmp_path / "thinking.ndjson"
    p.write_text('{"run":"r","task":"t","subtask":"s","text":"ok","ts":1}\nnot json\n[1]\n')
    assert [r["text"] for r in ThinkingLog(p).rows()] == ["ok"]


def test_planner_reasoning_is_recorded_for_the_run(fake, tmp_path):
    fake.chat_replies.append(chat_reply('[{"id":"a","title":"only","prompt":"p"}]', reasoning="plan it so"))
    orch = Orchestrator(tmp_path, place_fn=lambda *a, **k: _cloud())

    def engine(prompt):
        return chat("m", prompt, key="k").text

    rid = orch.submit("goal", engine)
    rows = [json.loads(ln) for ln in (tmp_path / "thinking.ndjson").read_text().splitlines()]
    assert [(r["run"], r["subtask"], r["text"]) for r in rows] == [(rid, "_plan", "plan it so")]


def test_orchestrator_work_records_start_and_thinking(fake, tmp_path):
    orch = Orchestrator(tmp_path, place_fn=lambda *a, **k: _cloud())
    rid = orch.submit("goal", lambda prompt: '[{"id":"a","title":"only","prompt":"p"}]')
    fake.chat_replies.append(chat_reply("done", reasoning="worked it out"))
    orch.work(_calls(1))
    assert any(r["event"] == "worker_start" and r["run"] == rid for r in orch.audit.rows())
    assert [r["text"] for r in ThinkingLog(tmp_path / "thinking.ndjson").rows()] == ["worked it out"]


def _cloud():
    from services.placement import Placement

    return Placement("cloud-OpenRouter", "test")
