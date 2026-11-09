"""Lanes (services/openrouter/lanes.py): per-lane keys that fall back to the one shared key, and priority slots so
chat is never queued behind subagent or background calls. A fake OpenRouter on loopback; no real key, no spend."""

from __future__ import annotations

import threading
import time

import pytest

from services.openrouter import chat, lanes
from services.orchestrator import chat as chat_turns

from fake_openrouter import FakeOpenRouter

KEYS = {"OPENROUTER_API_KEY": "k-shared", "OPENROUTER_API_KEY_CHAT": "k-chat", "OPENROUTER_API_KEY_BACKGROUND": "k-bg"}


# --- keys ----------------------------------------------------------------------------------------------------------
def test_each_lane_uses_its_own_key_and_falls_back_to_the_shared_one():
    assert lanes.key_for("chat", KEYS) == "k-chat"
    assert lanes.key_for("subagents", KEYS) == "k-shared", "no OPENROUTER_API_KEY_SUBAGENTS: the shared key"
    assert lanes.key_for("background", KEYS) == "k-bg"
    assert lanes.key_for("chat", {"OPENROUTER_API_KEY": "k-shared"}) == "k-shared"
    assert lanes.key_for("chat", {"OPENROUTER_API_KEY_CHAT": "  "}) == "", "blank is unset"
    assert lanes.status(KEYS) == {"chat": "lane", "subagents": "shared", "background": "lane"}
    assert lanes.status({}) == dict.fromkeys(lanes.LANES, "none")


def test_resolve_swaps_our_keys_per_lane_and_keeps_an_outside_key():
    assert lanes.resolve("k-shared", "chat", KEYS) == "k-chat"
    assert lanes.resolve("k-chat", "background", KEYS) == "k-bg", "an engine built in one lane spends the lane it runs in"
    assert lanes.resolve("", "subagents", KEYS) == "k-shared"
    assert lanes.resolve("k-from-keyring", "chat", KEYS) == "k-from-keyring"
    only_chat = {"OPENROUTER_API_KEY_CHAT": "k-chat"}
    assert lanes.resolve("k-chat", "subagents", only_chat) == "", "never another lane's key"
    assert lanes.any_key({"OPENROUTER_API_KEY_BACKGROUND": "k-bg"}) == "k-bg" and lanes.any_key({}) == ""


def test_scope_sets_the_lane_and_threads_start_in_the_default():
    assert lanes.current() == "subagents"
    seen = []
    with lanes.scope("chat"):
        assert lanes.current() == "chat"
        t = threading.Thread(target=lambda: seen.append(lanes.current()))
        t.start()
        t.join()
    assert seen == ["subagents"], "a run's worker thread is not chat just because chat started it"
    assert lanes.current() == "subagents"
    with pytest.raises(ValueError):
        lanes.scope("vip").__enter__()


@pytest.fixture
def fake(monkeypatch):
    for k in ("OPENROUTER_API_KEY", "OPENROUTER_API_KEY_CHAT", "OPENROUTER_API_KEY_SUBAGENTS", "OPENROUTER_API_KEY_BACKGROUND"):
        monkeypatch.delenv(k, raising=False)
    for k, v in KEYS.items():
        monkeypatch.setenv(k, v)
    with FakeOpenRouter() as f:
        monkeypatch.setenv("OC_OPENROUTER_BASE_URL", f.url)
        yield f


def auth(f) -> str:
    return {k.lower(): v for k, v in f.requests[-1]["headers"].items()}["authorization"]


def test_calls_spend_the_key_of_the_lane_they_run_in(fake, monkeypatch):
    chat("m", "hi", key=lanes.any_key())
    assert auth(fake) == "Bearer k-shared"
    with lanes.scope("chat"):
        chat("m", "hi", key=lanes.any_key())
    assert auth(fake) == "Bearer k-chat"
    monkeypatch.setattr(lanes, "_process_default", "background")
    chat("m", "hi", key=lanes.any_key())
    assert auth(fake) == "Bearer k-bg"
    chat("m", "hi", key="k-explicit")
    assert auth(fake) == "Bearer k-explicit"


def test_the_chat_engine_runs_in_the_chat_lane_even_with_only_a_chat_key(fake, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY")
    monkeypatch.delenv("OPENROUTER_API_KEY_BACKGROUND")
    chat_turns.openrouter_engine("m")([{"role": "user", "content": "hi"}])
    assert auth(fake) == "Bearer k-chat"


def test_with_no_key_anywhere_the_chat_engine_says_so(monkeypatch):
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY is not set"):
        chat_turns.openrouter_engine("m")([{"role": "user", "content": "hi"}])


# --- priority ------------------------------------------------------------------------------------------------------
def until(cond, what: str, timeout: float = 2.0) -> None:
    end = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > end:
            raise AssertionError(f"timed out waiting for {what}")
        time.sleep(0.002)


class Holder:
    """A call that takes a slot and keeps it until released."""

    def __init__(self, s: lanes.Scheduler, lane: str, order: list[str], name: str = ""):
        self.go, self.release = threading.Event(), threading.Event()
        self.name = name or lane

        def run():
            with s.slot(lane):
                order.append(self.name)
                self.go.set()
                self.release.wait(2)

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()


def waiting(s: lanes.Scheduler, lane: str) -> int:
    with s._cond:
        return len(s._waiting[lane])


def test_background_work_cannot_take_the_slots_kept_for_chat():
    s, order = lanes.Scheduler(capacity=3, chat_reserve=1), []
    bg = [Holder(s, "background", order, f"bg{i}") for i in range(3)]
    until(lambda: s.inflight()["background"] == 2 and waiting(s, "background") == 1, "two in flight, one queued")
    chat_call = Holder(s, "chat", order)
    assert chat_call.go.wait(1), "chat got the reserved slot while background was saturated"
    assert s.inflight() == {"chat": 1, "subagents": 0, "background": 2}
    for h in (*bg, chat_call):
        h.release.set()
    for h in (*bg, chat_call):
        h.thread.join(2)
    assert sorted(order) == ["bg0", "bg1", "bg2", "chat"] and s.inflight() == dict.fromkeys(lanes.LANES, 0)


def test_a_freed_slot_goes_to_chat_then_subagents_then_background():
    s, order = lanes.Scheduler(capacity=1, chat_reserve=0), []
    first = Holder(s, "subagents", order, "first")
    assert first.go.wait(1)
    bg = Holder(s, "background", order, "bg")
    until(lambda: waiting(s, "background") == 1, "background queued")
    sub = Holder(s, "subagents", order, "sub")
    until(lambda: waiting(s, "subagents") == 1, "subagent queued")
    chat_call = Holder(s, "chat", order, "chat")
    until(lambda: waiting(s, "chat") == 1, "chat queued")
    first.release.set()
    for h in (chat_call, sub, bg):  # each finishes as soon as it starts, in the order it was let in
        assert h.go.wait(2), h.name
        h.release.set()
        h.thread.join(2)
    assert order == ["first", "chat", "sub", "bg"]


def test_first_come_first_served_within_a_lane():
    s, order = lanes.Scheduler(capacity=1, chat_reserve=0), []
    gate = Holder(s, "chat", order, "gate")
    assert gate.go.wait(1)
    queued = []
    for i in range(3):
        queued.append(Holder(s, "subagents", order, f"s{i}"))
        until(lambda i=i: waiting(s, "subagents") == i + 1, f"s{i} queued")
    gate.release.set()
    for h in queued:
        assert h.go.wait(2)
        h.release.set()
        h.thread.join(2)
    assert order == ["gate", "s0", "s1", "s2"]


def test_scheduler_config_from_env():
    s = lanes.Scheduler.from_env({"OC_OPENROUTER_CONCURRENCY": "4", "OC_OPENROUTER_CHAT_RESERVE": "9"})
    assert (s.capacity, s.reserve) == (4, 3), "the reserve always leaves one slot for other lanes"
    s = lanes.Scheduler.from_env({"OC_OPENROUTER_CONCURRENCY": "x"})
    assert (s.capacity, s.reserve) == (6, 2)


def test_every_call_takes_a_slot_and_records_its_lane(fake, monkeypatch):
    from services.openrouter import post

    seen = []
    real = lanes.SCHEDULER.slot
    monkeypatch.setattr(lanes.SCHEDULER, "slot", lambda lane=None, wait=None: seen.append(lane) or real(lane, wait))
    with lanes.scope("chat"):
        _, row = post(f"{fake.url}/chat/completions", {"model": "m", "messages": []}, key=lanes.any_key(), sdk=True)
    assert seen == ["chat"] and row["lane"] == "chat"


def test_compaction_scoring_runs_in_the_chat_lane(fake):
    from services import compact

    assert compact.from_env(KEYS).ask is not None
    compact.openrouter_ask(lanes.key_for("subagents"))("m", [{"role": "user", "content": "x"}])
    assert auth(fake) == "Bearer k-chat"


def test_a_queued_call_gives_up_after_its_wait_and_is_recorded(fake, monkeypatch):
    from services.openrouter import OpenRouterError, post

    s, order = lanes.Scheduler(capacity=1, chat_reserve=0), []
    hold = Holder(s, "chat", order)
    assert hold.go.wait(1)
    with pytest.raises(lanes.SlotTimeout):
        with s.slot("subagents", wait=0.05):
            pass
    assert s.inflight() == {"chat": 1, "subagents": 0, "background": 0} and waiting(s, "subagents") == 0
    monkeypatch.setattr(lanes, "SCHEDULER", s)
    with pytest.raises(OpenRouterError) as e:
        post(f"{fake.url}/chat/completions", {"model": "m", "messages": []}, key=lanes.any_key(), timeout=0.05, sdk=True)
    assert e.value.error_class == "queue_timeout" and fake.requests == [], "never sent"
    hold.release.set()
    hold.thread.join(2)
