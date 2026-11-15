"""Context compaction for long chat threads, built on jev-compact (github.com/duketopceo/jev-compact, MIT).

When a thread's messages outgrow the budget, the older part is passed through jev-compact's retention filter:
each span (one message) is scored against the conversation's moving highlight, the spans that still matter are
kept verbatim, and runs of the rest become one-line tombstones. Nothing is destroyed: every span is saved under
`<OC_THREADS_DIR>/_compact/<thread>/` and a tombstone can be restored by its range (`s4-s9`), by the orchestrator
(`{"action":"restore","spans":"s4-s9"}`) or by hand (`python -m services.compact restore <thread> s4-s9`).

What Open Computer adds on top of jev-compact:

  * the scorer is a cheap model from Open Computer's own catalog (the Low effort preset unless OC_COMPACT_MODEL
    names another), called through `services.openrouter` so each call is attributed, costed and traced like any
    other (kind `compact`); with no key, or when a call fails, that span gets jev-compact's offline heuristic;
  * code and errors are never compacted away: a span with a code fence or an error signature is pinned;
  * a score cache keyed by (highlight, span text, model), so replaying the same thread gives the same context and
    costs nothing the second time;
  * the last OC_COMPACT_TAIL messages always go to the model as themselves, never folded into the summary block.

Config (env): OC_COMPACT (`off` disables), OC_COMPACT_BUDGET (tokens, default 24000; a target: pinned spans and
the tail are never dropped), OC_COMPACT_TAIL (default 6), OC_COMPACT_MODEL, OC_COMPACT_MAX_SCORED (model-scored
spans per compaction, newest first, default 60; older ones use the heuristic).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from jev_compact.compact import compact as jev_compact
from jev_compact.highlight import Highlight
from jev_compact.highlight import extract as extract_highlight
from jev_compact.scorer import HeuristicScorer
from jev_compact.spans import Span, est_tokens, segment
from jev_compact.store import SpanStore

from oc.obs import error_fields, get_logger

log = get_logger("compact")

DEFAULT_BUDGET = 24_000
DEFAULT_TAIL = 6
DEFAULT_MAX_SCORED = 60
FIT_TRIES = 4  # over-budget retries; pinned spans and the tail can still overshoot (logged, never dropped)
SPAN_VIEW_CHARS = 1_500  # the scorer sees a bounded view of each span, as jev-compact's own scorer does
RANGE = re.compile(r"^s\d{1,6}(?:-s\d{1,6})?$")
_CODE = re.compile(r"```|^(?: {4}|\t)\S", re.M)
_ERROR = re.compile(r"Traceback \(most recent call last\)|\b\w*(?:Error|Exception)\b:|\bFAILED\b|\bpanic(?:ked)?\b|exit code [1-9]")
RUN_NOTE = re.compile(r"^\[run [a-z0-9_]+\]")

# Same contract as jev-compact's OpenRouter chat scorer: JSON {rel 0-10, load 0-1}.
SCORER_PROMPT = (
    "You score conversation spans for context retention. "
    'Reply with JSON only: {"rel": <0-10>, "load": <0.0-1.0>}. '
    "rel = relevance to the current work highlight; "
    "load = probability the span carries facts still needed "
    "(paths, decisions, fixed errors) even if off-topic."
)
HEADER = (
    "Earlier messages in this chat, compacted by Open Computer. Kept messages are verbatim. A line like "
    "[[s4-s9 tombstoned ...]] stands for messages left out; if you need one, answer "
    '{"action":"restore","spans":"s4-s9"} and it will be shown to you.'
)

# The thread's own memory file: what the model wrote about this piece of
# work (goal, rules it was given, what done means, where progress lives).
# It bypasses the summarizer by construction — the writer and the
# summarizer are different actors — and is spliced back as a live message
# immediately before the user's turn, the most recent thing in context.
# Short is the only constraint (MEMORY_MAX_CHARS bounds a runaway file;
# a long file eats the budget this exists to save).
MEMORY_NAME = "memory.md"
MEMORY_HEADER = "Your memory file for this thread, as you last wrote it:"
MEMORY_MAX_CHARS = 4_000
MEMORY_NOTE = (
    "When the work spans many turns, keep your memory file (memory.md) short: the goal in a line, every "
    "rule or correction the user gave in their words, what counts as done, and where progress lives. "
    "It is shown back to you after every compaction and at the start of a resumed turn, so write it "
    "before the bulk of the work and update it when a rule or the plan changes. Answer "
    '{"action":"memory","text":"<full file>"} to write or replace it.'
)


def memory_path(tid: str, root_dir: Path | None = None) -> Path:
    """`<threads root>/_compact/<thread>/memory.md` — beside the span
    store, outside the workspace the sandbox mounts."""
    return (root_dir or root()) / tid / MEMORY_NAME


def read_memory(tid: str, root_dir: Path | None = None) -> str | None:
    """The thread's memory file as the model last wrote it, or None.
    Empty or absent is None: an empty file must not splice a blank
    reminder into every turn."""
    try:
        text = memory_path(tid, root_dir).read_text(encoding="utf-8")
    except OSError:
        return None
    text = text.strip()
    return text[:MEMORY_MAX_CHARS] if text else None


def write_memory(tid: str, text: str, root_dir: Path | None = None) -> bool:
    """Replace the memory file with the model's text. The model may only
    replace it whole — no appends, no deletes — so the file is always
    exactly what the model believes, and a hallucinated merge cannot
    leave a stale rule standing."""
    if not text or not text.strip():
        return False
    p = memory_path(tid, root_dir)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text.strip()[:MEMORY_MAX_CHARS], encoding="utf-8")
    except OSError:
        return False
    return True


def memory_message(tid: str, root_dir: Path | None = None) -> dict | None:
    """The memory file as a user-role message for the turn's message
    list — immediately before the live user turn, never inside the
    compacted block. None when the thread has no memory file."""
    body = read_memory(tid, root_dir)
    if body is None:
        return None
    return {"role": "user", "content": f"{MEMORY_HEADER}\n{body}"}


@dataclass(frozen=True)
class CompactConfig:
    budget: int = DEFAULT_BUDGET
    tail: int = DEFAULT_TAIL
    model: str = ""
    max_scored: int = DEFAULT_MAX_SCORED

    @staticmethod
    def from_env(env: dict[str, str] | None = None) -> CompactConfig | None:
        """None when OC_COMPACT=off. On by default: nothing happens until a thread outgrows the budget."""
        e = os.environ if env is None else env
        if e.get("OC_COMPACT", "").strip().lower() in ("off", "0", "false", "no"):
            return None

        def num(name: str, default: int, lo: int) -> int:
            try:
                return max(lo, int(e.get(name, default)))
            except ValueError:
                return default

        return CompactConfig(
            budget=num("OC_COMPACT_BUDGET", DEFAULT_BUDGET, 1000),
            tail=num("OC_COMPACT_TAIL", DEFAULT_TAIL, 2),
            model=e.get("OC_COMPACT_MODEL", "").strip() or default_model(),
            max_scored=num("OC_COMPACT_MAX_SCORED", DEFAULT_MAX_SCORED, 0),
        )


def default_model() -> str:
    """The cheap decider: the catalog's Low effort preset (its own fallbacks ride along on each call)."""
    from services.router.model_catalog import CATALOG

    return CATALOG.presets["low"]


def root() -> Path:
    threads = os.environ.get("OC_THREADS_DIR", str(Path.home() / ".local/share/oc/threads"))
    return Path(threads) / "_compact"


def pinned(text: str) -> bool:
    """Code and errors are what summaries mangle and agents need: never compact them away."""
    return bool(_CODE.search(text) or _ERROR.search(text))


class ScoreCache:
    """(highlight, span, model) -> (rel, load), one JSON file per thread. Makes replay deterministic and free."""

    def __init__(self, path: Path):
        self.path, self._lock = path, threading.Lock()
        try:
            self._d: dict[str, list[float]] = json.loads(path.read_text())
        except (OSError, ValueError):
            self._d = {}
        self.dirty = False

    @staticmethod
    def key(highlight: str, span: str, model: str) -> str:
        return hashlib.sha256(f"{model}\0{highlight}\0{span}".encode()).hexdigest()[:24]

    def get(self, k: str) -> tuple[float, float] | None:
        v = self._d.get(k)
        return (float(v[0]), float(v[1])) if isinstance(v, list) and len(v) == 2 else None

    def put(self, k: str, v: tuple[float, float]) -> None:
        with self._lock:
            self._d[k] = [round(v[0], 4), round(v[1], 4)]
            self.dirty = True

    def save(self) -> None:
        if not self.dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".tmp-")
        with os.fdopen(fd, "w") as f:
            json.dump(self._d, f, sort_keys=True)
        os.replace(tmp, self.path)
        self.dirty = False


Ask = Callable[[str, list[dict]], str]  # (model, messages) -> reply text; raises on failure


class ModelScorer:
    """jev-compact's Scorer protocol over Open Computer's OpenRouter client, cached, pinned, heuristic on failure."""

    parallel_ok = True  # I/O-bound; jev-compact fans out across a small thread pool

    def __init__(self, model: str, ask: Ask | None, cache: ScoreCache, total_spans: int, model_ids: set[str]):
        self.model, self.ask, self.cache = model, ask, cache
        self.heuristic = HeuristicScorer(total_spans)
        self.model_ids = model_ids  # spans worth a model call; the rest get the heuristic
        self.calls = self.failures = 0
        self._lock = threading.Lock()
        self._memo: dict[tuple[str, str], tuple[float, float]] = {}  # one answer per span per fit, failures too

    def score(self, span: Span, highlight: Highlight) -> tuple[float, float]:
        k = (span.id, highlight.text)
        if k not in self._memo:
            self._memo[k] = self._raw(span, highlight)
        rel, load = self._memo[k]
        return rel, (1.0 if pinned(span.text) else load)

    def _raw(self, span: Span, highlight: Highlight) -> tuple[float, float]:
        if self.ask is None or span.id not in self.model_ids:
            return self.heuristic.score(span, highlight)
        k = ScoreCache.key(highlight.text, span.text, self.model)
        hit = self.cache.get(k)
        if hit is not None:
            return hit
        with self._lock:
            self.calls += 1
        try:
            raw = self.ask(self.model, [
                {"role": "system", "content": SCORER_PROMPT},
                {"role": "user", "content": f"{highlight.text}\n\nSPAN ({span.kind}):\n{span.text[:SPAN_VIEW_CHARS]}"},
            ])
            got = _parse_score(raw)
        except Exception as e:  # a failed or garbled score never blocks a turn: this span falls back
            with self._lock:
                self.failures += 1
            log.warning("compact score fell back to heuristic", span=span.id, **error_fields(e))
            return self.heuristic.score(span, highlight)
        self.cache.put(k, got)
        return got


def _parse_score(raw: str) -> tuple[float, float]:
    i, j = raw.find("{"), raw.rfind("}")
    if i == -1 or j <= i:
        raise ValueError("no JSON object in score reply")
    obj = json.loads(raw[i : j + 1])
    rel, load = float(obj["rel"]) / 10.0, float(obj["load"])
    return max(0.0, min(1.0, rel)), max(0.0, min(1.0, load))


def openrouter_ask(key: str) -> Ask:
    from services.openrouter import chat as or_chat
    from services.openrouter import lanes

    def ask(model: str, messages: list[dict]) -> str:
        with lanes.scope("chat"):  # scoring sits on the owner's turn: chat's key, first in line
            return or_chat(model, messages=messages, key=key, timeout=30, kind="compact", effort="low",
                           temperature=0, max_tokens=40).text

    return ask


@dataclass
class Compacted:
    messages: list[dict]
    compacted: bool
    tokens_before: int
    tokens_after: int
    kept: int = 0
    tombstones: int = 0
    calls: int = 0
    fallbacks: int = 0


def _tokens(messages: list[dict]) -> int:
    return sum(est_tokens(str(m.get("content", ""))) for m in messages)


def _event(m: dict) -> dict:
    text = str(m.get("content", ""))
    if m.get("role") == "assistant":
        return {"kind": "assistant_text", "text": text}
    if RUN_NOTE.match(text):
        return {"kind": "tool", "text": text}  # what a run produced: one span, scored and kept as a unit
    return {"kind": "user_turn", "text": text}


class Compactor:
    def __init__(self, cfg: CompactConfig, ask: Ask | None = None, store_root: Path | None = None):
        self.cfg, self.ask, self.root = cfg, ask, store_root or root()

    def store(self, tid: str) -> SpanStore:
        return SpanStore(self.root, tid)

    def fit(self, tid: str, messages: list[dict]) -> Compacted:
        """The messages to send: unchanged when they fit; otherwise system prefix + one compacted block + tail."""
        before = _tokens(messages)
        if before <= self.cfg.budget:
            return Compacted(messages, False, before, before)
        n_sys = next((i for i, m in enumerate(messages) if m.get("role") != "system"), len(messages))
        prefix, conv = messages[:n_sys], messages[n_sys:]
        tail_n = min(self.cfg.tail, len(conv))
        if len(conv) <= tail_n:
            return Compacted(messages, False, before, before)
        spans = segment([_event(m) for m in conv])  # one message, one span: ids stay stable as the thread grows
        head, tail = spans[: len(conv) - tail_n], conv[len(conv) - tail_n :]
        highlight = extract_highlight(spans)
        cache = ScoreCache(self.root / tid / "scores.json")
        newest = {s.id for s in head[-self.cfg.max_scored :]} if self.cfg.max_scored else set()
        scorer = ModelScorer(self.cfg.model, self.ask, cache, len(spans), newest)
        # jev-compact treats the budget as a target (receipts and span labels cost more than it counts), so
        # tighten the head's share by the overshoot and run again. Scores are cached: a retry costs no calls.
        budget = max(0, self.cfg.budget - _tokens(prefix) - _tokens(tail) - est_tokens(HEADER))
        for _ in range(FIT_TRIES):
            res = jev_compact(head, highlight, scorer, budget, tail_keep=0)
            out = [*prefix, {"role": "system", "content": f"{HEADER}\n\n{res.text}"}, *tail]
            after = _tokens(out)
            if after <= self.cfg.budget or budget == 0:
                break
            budget = max(0, budget - (after - self.cfg.budget) - 16)
        cache.save()
        self.store(tid).save(spans, highlight, res.tombstoned, self.root / tid / "thread")
        log.info("thread compacted", thread=tid, tokens_before=before, tokens_after=after, kept=len(res.kept_ids),
                 tombstones=len(res.tombstoned), calls=scorer.calls, fallbacks=scorer.failures)
        return Compacted(out, True, before, after, len(res.kept_ids), len(res.tombstoned), scorer.calls, scorer.failures)

    def restore(self, tid: str, rng: str) -> str | None:
        """The verbatim text of a tombstoned range, or None when the range is malformed or unknown."""
        if not RANGE.match(rng or ""):
            return None
        return self.store(tid).get_range(rng)


def from_env(env: dict[str, str] | None = None) -> Compactor | None:
    """Compaction with the cheap model when an OpenRouter key is set, the offline heuristic otherwise."""
    cfg = CompactConfig.from_env(env)
    if cfg is None:
        return None
    from services.openrouter import lanes

    key = lanes.key_for("chat", env)
    return Compactor(cfg, openrouter_ask(key) if key else None)
