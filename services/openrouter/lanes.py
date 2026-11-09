"""Lanes: which OpenRouter key a call spends, and who goes first when calls queue.

Three lanes, highest priority first:

  chat        the owner's chat turn (the orchestrator replying, plus compaction scoring on that path);
  subagents   run work: planning, routing decisions, steps (the default for any call not marked otherwise);
  background  automations and the durable worker (`python -m services.durable.worker` runs in this lane).

Keys. Each lane may have its own key; a lane without one uses the shared key:

  OPENROUTER_API_KEY_CHAT / OPENROUTER_API_KEY_SUBAGENTS / OPENROUTER_API_KEY_BACKGROUND -> OPENROUTER_API_KEY

The key is picked when the call is sent, from the lane the call runs in. A caller that passes a key this module did
not read from the environment (a test double, a key from the keyring flow) keeps it. What separate keys buy is
OpenRouter-side spend caps and per-key usage, so background work can be capped or cut off without touching chat.
They do not add rate limit: OpenRouter limits by account, not key. Crowding is handled here instead:

Priority. At most OC_OPENROUTER_CONCURRENCY calls (default 6) are in flight in one process. Subagent and
background calls together may hold at most that minus OC_OPENROUTER_CHAT_RESERVE (default 2), so chat always has
a free slot; when a slot frees, a waiting chat call goes first, then subagents, then background (first come first
served within a lane). This is per process: the durable worker is its own process with its own slots.
"""

from __future__ import annotations

import itertools
import os
import threading
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

LANES = ("chat", "subagents", "background")  # priority order
DEFAULT_LANE = "subagents"
SHARED_ENV = "OPENROUTER_API_KEY"
LANE_ENV = {lane: f"{SHARED_ENV}_{lane.upper()}" for lane in LANES}

_lane: ContextVar[str | None] = ContextVar("oc_or_lane", default=None)
_process_default = DEFAULT_LANE


def _env(env: dict[str, str] | None) -> dict[str, str] | os._Environ[str]:
    return os.environ if env is None else env


def current() -> str:
    """The lane of the running code: an enclosing `scope`, else this process's default."""
    return _lane.get() or _process_default


def set_process_default(lane: str) -> None:
    """For a whole process that only does one kind of work (the durable worker is `background`)."""
    global _process_default
    if lane not in LANES:
        raise ValueError(f"unknown lane {lane!r}")
    _process_default = lane


@contextmanager
def scope(lane: str) -> Iterator[None]:
    """Run a block in a lane. Threads started inside do not inherit it (contextvars), so a run's workers drop back
    to the default lane on their own threads, which is what we want."""
    if lane not in LANES:
        raise ValueError(f"unknown lane {lane!r}")
    token = _lane.set(lane)
    try:
        yield
    finally:
        _lane.reset(token)


def key_for(lane: str, env: dict[str, str] | None = None) -> str:
    e = _env(env)
    return (e.get(LANE_ENV[lane], "") or "").strip() or (e.get(SHARED_ENV, "") or "").strip()


def key_source(lane: str, env: dict[str, str] | None = None) -> str:
    """`lane` (its own key), `shared` (OPENROUTER_API_KEY) or `none`. Names only; never the key."""
    e = _env(env)
    if (e.get(LANE_ENV[lane], "") or "").strip():
        return "lane"
    return "shared" if (e.get(SHARED_ENV, "") or "").strip() else "none"


def any_key(env: dict[str, str] | None = None) -> str:
    """A key if any lane can reach OpenRouter, else "". For "is OpenRouter configured?" checks and as the key an
    engine is built with; `resolve` swaps it for the right lane's key when the call is sent."""
    e = _env(env)
    for name in (SHARED_ENV, *LANE_ENV.values()):
        v = (e.get(name, "") or "").strip()
        if v:
            return v
    return ""


def resolve(key: str, lane: str | None = None, env: dict[str, str] | None = None) -> str:
    """The key to send: the lane's own key (or the shared one) when `key` came from this environment or is empty;
    an outside key unchanged."""
    e = _env(env)
    ours = {(e.get(n, "") or "").strip() for n in (SHARED_ENV, *LANE_ENV.values())} - {""}
    if key and key not in ours:
        return key
    return key_for(lane or current(), env) or key


def status(env: dict[str, str] | None = None) -> dict[str, str]:
    """lane -> key source, for diagnostics."""
    return {lane: key_source(lane, env) for lane in LANES}


class Scheduler:
    """Priority slots for in-flight calls. Chat is never queued behind subagent or background work."""

    def __init__(self, capacity: int = 6, chat_reserve: int = 2):
        self.capacity = max(1, capacity)
        self.reserve = max(0, min(chat_reserve, self.capacity - 1))
        self._cond = threading.Condition()
        self._inflight = dict.fromkeys(LANES, 0)
        self._waiting: dict[str, deque[int]] = {lane: deque() for lane in LANES}
        self._tickets = itertools.count()

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Scheduler:
        e = _env(env)

        def num(name: str, default: int) -> int:
            try:
                return int(e.get(name, default))
            except ValueError:
                return default

        return cls(num("OC_OPENROUTER_CONCURRENCY", 6), num("OC_OPENROUTER_CHAT_RESERVE", 2))

    def inflight(self) -> dict[str, int]:
        with self._cond:
            return dict(self._inflight)

    def _may_run(self, lane: str, ticket: int) -> bool:
        if self._waiting[lane][0] != ticket:
            return False  # first come first served within a lane
        if sum(self._inflight.values()) >= self.capacity:
            return False
        others = self._inflight["subagents"] + self._inflight["background"]
        if lane != "chat" and others >= self.capacity - self.reserve:
            return False
        rank = LANES.index(lane)
        return not any(self._waiting[hi] for hi in LANES[:rank])  # a higher lane waiting goes first

    @contextmanager
    def slot(self, lane: str | None = None) -> Iterator[None]:
        lane = lane or current()
        with self._cond:
            ticket = next(self._tickets)
            self._waiting[lane].append(ticket)
            try:
                while not self._may_run(lane, ticket):
                    self._cond.wait()
                self._inflight[lane] += 1
            finally:
                self._waiting[lane].remove(ticket)
                self._cond.notify_all()  # the next waiter in this lane may fit now, or must re-check
        try:
            yield
        finally:
            with self._cond:
                self._inflight[lane] -= 1
                self._cond.notify_all()


SCHEDULER = Scheduler.from_env()
