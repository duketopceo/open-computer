"""Decisions-endpoint client — one wire shape, two engines.

Primary: perplexity/pplx-decider (best tier accuracy + cheapest in bench).
Fallback: typesafe/jev-1.13 (faster; local-shim compatible).

Validated against the bench wire: POST {state, questions, model?} ->
{answers: {<q>: {choice, confidence}}, model, usage.cost}.
"""

from __future__ import annotations

import re
import time

from oc.obs import redact_str
from services.openrouter import post as or_post

from .policy_table import QUESTIONS

DECIDER_MODEL = "perplexity/pplx-decider-v1.1-27b"
JEV_MODEL = "typesafe/jev-1.13"
DEFAULT_BASE = "https://openrouter.ai/api"


class EngineDown(Exception):
    """Endpoint unreachable, HTTP error, or unparseable answer.

    `status` is the HTTP status when there was one; `reason` is a short, redacted, human-readable cause."""

    def __init__(self, msg: str, *, status: int | None = None, reason: str = ""):
        super().__init__(msg)
        self.status, self.reason = status, reason


_MESSAGE_RE = re.compile(r'"message"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _reason(e: Exception) -> str:
    """The provider's own words for a failed call (its error message), redacted and cut to 160 characters."""
    text = str(e)
    if getattr(e, "status", None):
        detail = text.split(f"HTTP {e.status}: ", 1)[-1]
        m = _MESSAGE_RE.search(detail)
        text = m.group(1) if m else detail
    return redact_str(text)[:160]


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
    t0 = time.monotonic()
    try:
        d, _ = or_post(
            f"{base_url.rstrip('/')}/alpha/decisions", payload, key=api_key, timeout=timeout,
            kind="decision", model=model or DECIDER_MODEL, body_fields=False,
        )
    except Exception as e:
        raise EngineDown(str(e), status=getattr(e, "status", None), reason=_reason(e)) from e
    ms = (time.monotonic() - t0) * 1000
    answers = d.get("answers") or {}
    route = (answers.get("route") or {}).get("choice")
    tier = (answers.get("tier") or {}).get("choice")
    if not route or not tier:
        raise EngineDown(f"invalid answers: {answers!r}", reason="invalid answers")
    return {
        "route": route,
        "tier": tier,
        "confidence": (answers.get("route") or {}).get("confidence"),
        "model_used": d.get("model"),
        "cost": (d.get("usage") or {}).get("cost", 0.0) or 0.0,
        "ms": ms,
    }
