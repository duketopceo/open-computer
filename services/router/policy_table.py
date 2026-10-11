"""Route/tier space + candidate-model table + deterministic last resort.

The route space is wisp-derived (the bench corpus uses it). The tier→model
table is what a decision engine picks INSIDE — the engine chooses the tier,
the table maps tier to concrete candidate models.

`offline_decide` is the deterministic fallback when every engine is down:
keyword heuristics, honestly labeled engine="table" — never silently a model.
"""

from __future__ import annotations

import re

ROUTES = {
    "launch": "open, start, or close an application and nothing else",
    "tool": "a desktop/system action — window ops, workspace switch, type text, screenshot, notify, run a command, find files",
    "agent": "spawn a background agent for a coding, research, or multi-step task",
    "learn": "the user wants the assistant to learn or remember how to do something",
    "act": "a multi-step or in-app desktop task",
    "dictation": "the user wants to dictate",
    "answer": "the user is asking a question or chatting",
    "clarify": "the request is too ambiguous to act on",
}

TIERS = {
    "local": "trivial task a small local model handles fine",
    "cheap": "routine task needing a hosted flash-class model",
    "mid": "hard task needing a strong mid-tier model",
    "frontier": "exceptional task only the strongest frontier model can handle",
}

QUESTIONS = {
    "route": {
        "type": "choice",
        "instructions": "What kind of request is this? Pick the single best route.",
        "criteria": ROUTES,
    },
    "tier": {
        "type": "choice",
        "instructions": "Which model tier should serve this task? Pick the cheapest tier that handles it well.",
        "criteria": TIERS,
    },
}

# tier -> candidate models the placement layer may bind to
TIER_MODELS = {
    "local": ["typesafe/jev-1.13", "local/ollama"],
    "cheap": ["google/gemini-2.5-flash", "deepseek/deepseek-v4.1-flash"],
    "mid": ["x-ai/grok-4.7", "anthropic/claude-sonnet-4.5"],
    "frontier": ["anthropic/claude-opus-4.5"],
}

_KW = [
    (re.compile(r"\b(agent|spawn|delegate|background)\b", re.I), "agent"),
    (re.compile(r"\b(learn|remember|teach|add a skill)\b", re.I), "learn"),
    (re.compile(r"\b(dictate|dictation|type this)\b", re.I), "dictation"),
    (re.compile(r"\b(open|launch|start|close)\s+[\w-]+$", re.I), "launch"),
    (re.compile(r"\b(click|screenshot|window|workspace|type|find file|notify)\b", re.I), "tool"),
    (re.compile(r"[?!]\s*$", re.I), "answer"),
]


def candidates(task_type: str, complexity: str, latency: str) -> list[str]:
    """task_type x complexity x latency -> concrete model candidates."""
    tier = (
        "local"
        if latency == "low" or complexity == "trivial"
        else complexity if complexity in TIERS else "cheap"
    )
    return TIER_MODELS.get(tier, TIER_MODELS["cheap"])


def offline_decide(utterance: str) -> dict:
    """Deterministic fallback — declared engine='table', low confidence."""
    route = next((r for pat, r in _KW if pat.search(utterance)), "clarify")
    return {
        "route": route,
        "tier": "local" if route in ("launch", "dictation", "clarify", "answer") else "cheap",
        "confidence": 0.0,
    }
