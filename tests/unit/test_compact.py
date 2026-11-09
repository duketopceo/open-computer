"""Compaction of long chat threads (services/compact over jev-compact): a fixed transcript fits the budget with no
code or error lost, tombstones restore verbatim, replay is deterministic with the model mocked, failures fall back,
and the chat turn can ask for a restore. No network: the model is a function."""

from __future__ import annotations

import json
import re

import pytest

from services import compact as c
from services.opsview import threads
from services.orchestrator import chat

CODE = "```python\ndef parse_lidar(frame):\n    return [p for p in frame.points if p.z > 0]\n```"
TRACE = 'Traceback (most recent call last):\n  File "lidar/parse.py", line 12, in parse_lidar\nKeyError: \'points\''
FILLER = "The lunar regolith survey covered crater rims, albedo maps and seismic notes from the old missions. " * 6


def transcript() -> list[dict]:
    """40 messages: a long moon-research stretch, then lidar code work with one code block and one traceback."""
    msgs = [{"role": "system", "content": chat.SYSTEM}]
    for i in range(14):
        msgs.append({"role": "user", "content": f"Moon question {i}: {FILLER}"})
        msgs.append({"role": "assistant", "content": f"Moon answer {i}: {FILLER}"})
    msgs.append({"role": "user", "content": "Switch to the lidar parser. Here is what I have:\n" + CODE})
    msgs.append({"role": "assistant", "content": "Running it on the sample frame now."})
    msgs.append({"role": "user", "content": "[run goal_0000000a] Goal: run the lidar parser\nStatus: failed\n- run (FAILED): " + TRACE})
    msgs.append({"role": "assistant", "content": "The frame has no points key. " + FILLER})
    for i in range(8):
        msgs.append({"role": "user", "content": f"Lidar follow-up {i}: what about the point cloud filters? {FILLER}"})
        msgs.append({"role": "assistant", "content": f"Lidar reply {i}: {FILLER}"})
    return msgs


class Model:
    """A mocked cheap model: scores by topic, records each call."""

    def __init__(self, fail_on: str = "", careless: bool = False):
        self.calls: list[str] = []
        self.fail_on, self.careless = fail_on, careless

    def __call__(self, model: str, messages: list[dict]) -> str:
        span = messages[-1]["content"].split("SPAN (", 1)[1]
        self.calls.append(span[:40])
        if self.fail_on and self.fail_on in span:
            raise RuntimeError("HTTP 503 from the provider")
        if self.careless and ("```" in span or "Traceback" in span):
            return json.dumps({"rel": 0, "load": 0.0})  # a cheap model that undervalues exactly what matters
        rel = 8 if "lidar" in span.lower() else 1
        return json.dumps({"rel": rel, "load": 0.1})


CFG = c.CompactConfig(budget=3000, tail=4, model="vendor/cheap-1", max_scored=60)


def text(msgs: list[dict]) -> str:
    return "\n".join(str(m["content"]) for m in msgs)


def test_a_fixed_transcript_fits_the_budget_and_keeps_every_code_block_and_error(tmp_path):
    msgs = transcript()
    model = Model(careless=True)
    out = c.Compactor(CFG, model, tmp_path).fit("th_00000001", msgs)
    assert out.compacted and out.tokens_before > 2 * CFG.budget
    assert out.tokens_after <= CFG.budget, (out.tokens_after, CFG.budget)
    sent = text(out.messages)
    assert CODE in sent and TRACE in sent, "code and errors are pinned verbatim"
    assert out.messages[0]["content"] == chat.SYSTEM, "the system prompt is never compacted"
    assert out.messages[-4:] == msgs[-4:], "the tail goes as itself"
    assert out.messages[1]["content"].startswith(c.HEADER) and "tombstoned" in out.messages[1]["content"]
    assert out.tombstones >= 1 and out.calls == len(model.calls) > 0


def test_short_threads_pass_through_with_no_model_call(tmp_path):
    msgs = transcript()[:5]
    model = Model()
    out = c.Compactor(CFG, model, tmp_path).fit("th_00000001", msgs)
    assert not out.compacted and out.messages == msgs and model.calls == []


def test_every_tombstone_restores_verbatim(tmp_path):
    msgs = transcript()
    comp = c.Compactor(CFG, Model(), tmp_path)
    block = comp.fit("th_00000001", msgs).messages[1]["content"]
    ranges = re.findall(r"\[\[(s\d+(?:-s\d+)?) tombstoned", block)
    assert ranges
    conv = msgs[1:]
    for rng in ranges:
        lo, _, hi = rng.partition("-")
        a, b = int(lo[1:]), int((hi or lo)[1:])
        assert comp.restore("th_00000001", rng) == "\n\n".join(m["content"] for m in conv[a : b + 1])
    assert comp.restore("th_00000001", "../etc") is None
    assert comp.restore("th_00000001", "s9999") is None


def test_replay_is_deterministic_and_the_second_pass_costs_nothing(tmp_path):
    msgs = transcript()
    first_model, second_model = Model(), Model()
    a = c.Compactor(CFG, first_model, tmp_path).fit("th_00000001", msgs)
    b = c.Compactor(CFG, second_model, tmp_path).fit("th_00000001", msgs)
    assert a.messages == b.messages
    assert first_model.calls and second_model.calls == [], "cached scores: same inputs, no new spend"
    fresh = c.Compactor(CFG, Model(), tmp_path / "other").fit("th_00000001", msgs)
    assert fresh.messages == a.messages, "same model answers give the same context from a cold cache"


def test_a_failed_score_falls_back_to_the_heuristic_and_still_fits(tmp_path):
    msgs = transcript()
    out = c.Compactor(CFG, Model(fail_on="Moon question 3"), tmp_path).fit("th_00000001", msgs)
    assert out.fallbacks == 1 and out.tokens_after <= CFG.budget
    assert CODE in text(out.messages)


def test_with_no_key_the_heuristic_compacts_offline(tmp_path):
    out = c.Compactor(CFG, None, tmp_path).fit("th_00000001", transcript())
    assert out.compacted and out.calls == 0 and out.tokens_after <= CFG.budget
    assert CODE in text(out.messages) and TRACE in text(out.messages)


def test_only_the_newest_spans_are_model_scored(tmp_path):
    model = Model()
    c.Compactor(c.CompactConfig(budget=3000, tail=4, model="m", max_scored=5), model, tmp_path).fit("th_1", transcript())
    assert len(model.calls) == 5


@pytest.mark.parametrize("raw", ["", "no json", '{"rel": "x", "load": 1}', '{"rel": 5}'])
def test_garbled_scores_fall_back(tmp_path, raw):
    out = c.Compactor(CFG, lambda m, msgs: raw, tmp_path).fit("th_1", transcript())
    assert out.fallbacks == out.calls > 0 and out.tokens_after <= CFG.budget


def test_pins_cover_code_and_errors_not_prose():
    assert c.pinned(CODE) and c.pinned(TRACE) and c.pinned("ValueError: bad frame") and c.pinned("tests FAILED")
    assert not c.pinned(FILLER) and not c.pinned("No errors today.")


def test_config_defaults_to_the_catalogs_cheap_preset_and_can_be_turned_off():
    from services.router.model_catalog import CATALOG

    assert c.CompactConfig.from_env({}).model == CATALOG.presets["low"]
    cfg = c.CompactConfig.from_env({"OC_COMPACT_MODEL": "vendor/other", "OC_COMPACT_BUDGET": "9", "OC_COMPACT_TAIL": "x"})
    assert (cfg.model, cfg.budget, cfg.tail) == ("vendor/other", 1000, c.DEFAULT_TAIL)
    assert c.CompactConfig.from_env({"OC_COMPACT": "off"}) is None
    assert c.from_env({"OC_COMPACT": "off"}) is None
    assert c.from_env({}).ask is None, "no key: heuristic only, nothing leaves the box"


def test_openrouter_ask_sends_a_cheap_attributed_call(monkeypatch):
    seen = {}

    def fake_chat(model, **kw):
        seen.update(model=model, **kw)

        class R:
            text = '{"rel": 7, "load": 0.5}'

        return R()

    monkeypatch.setattr("services.openrouter.chat", fake_chat)
    assert c.openrouter_ask("k-test")("vendor/cheap-1", [{"role": "user", "content": "x"}]) == '{"rel": 7, "load": 0.5}'
    assert (seen["model"], seen["kind"], seen["key"], seen["temperature"], seen["effort"]) == ("vendor/cheap-1", "compact", "k-test", 0, "low")


def test_restore_cli(tmp_path, monkeypatch, capsys):
    from services.compact.__main__ import main

    monkeypatch.setenv("OC_THREADS_DIR", str(tmp_path))
    msgs = transcript()
    block = c.Compactor(CFG, None).fit("th_00000001", msgs).messages[1]["content"]
    rng = re.search(r"\[\[(s\d+(?:-s\d+)?) tombstoned", block).group(1)
    assert main(["restore", "th_00000001", rng]) == 0
    assert capsys.readouterr().out.strip().startswith("Moon")
    assert main(["restore", "th_00000001", "s999"]) == 1
    assert main([]) == 2


# --- the chat turn -------------------------------------------------------------------------------------------------
def test_parse_decision_reads_restore():
    assert chat.parse_decision('{"action":"restore","spans":"s4-s9"}') == chat.Decision("restore", "", spans="s4-s9")
    assert chat.parse_decision('{"action":"restore","spans":"../x"}').action == "reply"


@pytest.fixture
def tdir(tmp_path, monkeypatch):
    monkeypatch.setenv("OC_THREADS_DIR", str(tmp_path / "threads"))
    monkeypatch.setenv("OC_GOALS_DIR", str(tmp_path / "goals"))
    return tmp_path


def long_thread() -> str:
    t = threads.create("Moon question 0: " + FILLER, {"effort": "low", "model": "m", "budget": 0.1})
    for i in range(14):
        threads.append(t["id"], "assistant", f"Moon answer {i}: {FILLER}")
        threads.append(t["id"], "user", f"Moon question {i + 1}: {FILLER}")
    threads.append(t["id"], "assistant", "Noted.")
    threads.append(t["id"], "user", "What did the third moon answer say?")
    return t["id"]


def test_a_long_thread_is_compacted_and_the_model_can_restore_a_span(tdir):
    tid = long_thread()
    seen: list[list[dict]] = []
    answers = []

    def engine(messages):
        seen.append(messages)
        if not answers:
            rng = re.search(r"\[\[(s\d+(?:-s\d+)?) tombstoned", messages[1]["content"]).group(1)
            answers.append(rng)
            return json.dumps({"action": "restore", "spans": rng})
        return json.dumps({"action": "reply", "text": "It covered crater rims."})

    d = threads.Deps(engine=engine, start_run=lambda g: "goal_1", load_run=lambda r: None, wait_run=lambda r: "done",
                     compact=c.Compactor(CFG, None))
    threads.run_turn(tid, d)
    assert threads.get(tid)["messages"][-1]["text"] == "It covered crater rims."
    assert len(seen) == 2, "a restore is not a pass: the model answered again"
    assert c.HEADER in seen[0][1]["content"]
    last = seen[1][-1]
    assert last["role"] == "system" and last["content"].startswith(chat.RESTORE_NOTE) and "Moon" in last["content"]
    assert c.est_tokens(text(seen[0])) <= CFG.budget


def test_restores_are_capped_per_turn(tdir):
    tid = long_thread()
    calls = []

    def engine(messages):
        calls.append(1)
        return json.dumps({"action": "restore", "spans": "s1"})

    d = threads.Deps(engine=engine, start_run=lambda g: "goal_1", load_run=lambda r: None, wait_run=lambda r: "done",
                     compact=c.Compactor(CFG, None))
    threads.run_turn(tid, d)
    assert len(calls) == threads.MAX_RESTORES + 1
    assert "too many restores" in threads.get(tid)["messages"][-1]["text"]


def test_compaction_off_leaves_turns_as_they_were(tdir):
    tid = long_thread()
    seen = []
    d = threads.Deps(engine=lambda m: seen.append(m) or json.dumps({"action": "reply", "text": "ok"}),
                     start_run=lambda g: "goal_1", load_run=lambda r: None, wait_run=lambda r: "done")
    threads.run_turn(tid, d)
    assert len(seen[0]) == 1 + len(threads.get(tid)["messages"]) - 1  # system + every message before the reply
