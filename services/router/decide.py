"""Decisions-endpoint client — one wire shape, two engines.

Primary: perplexity/pplx-decider (best tier accuracy + cheapest in bench).
Fallback: typesafe/jev-1.13 (faster; local-shim compatible).

Validated against the bench wire: POST {state, questions, model?} ->
{answers: {<q>: {choice, confidence}}, model, usage.cost}.
"""

from __future__ import annotations

import json
import time
import urllib.request

from .policy_table import QUESTIONS

DECIDER_MODEL = "perplexity/pplx-decider-v1.1-27b"
JEV_MODEL = "typesafe/jev-1.13"
DEFAULT_BASE = "https://openrouter.ai/api"


class EngineDown(Exception):
    """Endpoint unreachable, HTTP error, or unparseable answer."""


def ask_decisions(
    state: str,
    base_url: str = DEFAULT_BASE,
    api_key: str = "",
    model: str | None = None,
    questions: dict | None = None,
    timeout: float = 90,
) -> dict:
    payload: dict = {"state": state, "questions": questions or QUESTIONS}
    if model:
        payload["model"] = model
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/alpha/decisions",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {api_key}"} if api_key else {}),
        },
        method="POST",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read())
    except Exception as e:
        raise EngineDown(str(e)) from e
    ms = time.time() - t0
    answers = d.get("answers") or {}
    route = (answers.get("route") or {}).get("choice")
    tier = (answers.get("tier") or {}).get("choice")
    if not route or not tier:
        raise EngineDown(f"invalid answers: {answers!r}")
    return {
        "route": route,
        "tier": tier,
        "confidence": (answers.get("route") or {}).get("confidence"),
        "model_used": d.get("model"),
        "cost": (d.get("usage") or {}).get("cost", 0.0) or 0.0,
        "ms": ms,
    }
