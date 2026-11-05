"""Decisions-endpoint client — one wire shape, two engines.

Primary: perplexity/pplx-decider (best tier accuracy + cheapest in bench), or OC_DECIDER_MODEL.
Fallbacks: OC_DECIDER_FALLBACKS (comma-separated model ids), then always typesafe/jev-1.13 (faster;
local-shim compatible). `decider_chain` is the order the router tries them in, one engine per model.

Validated against the bench wire: POST {state, questions, model?} ->
{answers: {<q>: {choice, confidence}}, model, usage.cost}.
"""

from __future__ import annotations

import os
import time

from services.openrouter import post as or_post

from .policy_table import QUESTIONS

DECIDER_MODEL = "perplexity/pplx-decider-v1.1-27b"
JEV_MODEL = "typesafe/jev-1.13"
DEFAULT_BASE = "https://openrouter.ai/api"


def decider_chain() -> list[tuple[str, str]]:
    """(engine name, model) in try order: the decider, its configured fallbacks, then jev. Duplicates dropped."""
    primary = os.environ.get("OC_DECIDER_MODEL", "").strip() or DECIDER_MODEL
    extra = [m.strip() for m in os.environ.get("OC_DECIDER_FALLBACKS", "").split(",") if m.strip()]
    models = list(dict.fromkeys([primary, *extra, JEV_MODEL]))
    names = ["decider", *(f"decider-fallback-{i}" for i in range(1, len(models)))]
    out = [(n, m) for n, m in zip(names, models)]
    return [(n if m != JEV_MODEL else "jev", m) for n, m in out]


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
    t0 = time.time()
    try:
        d, _ = or_post(
            f"{base_url.rstrip('/')}/alpha/decisions", payload, key=api_key, timeout=timeout,
            kind="decision", model=model or DECIDER_MODEL, body_fields=False,
        )
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
