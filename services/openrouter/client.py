"""The one OpenRouter HTTP client: attribution on every call, a complete local record of every request.

Provider-reported `usage.cost` is the cost truth (OpenRouter always returns it; no price table here). A call
with no reported cost is recorded `cost_source: "none"` and warned, never guessed. Recording failures never
affect the call. Prompt/completion text is stored only with OC_OBS_CAPTURE_CONTENT=1 (and still redacted).

Env: OC_OPENROUTER_BASE_URL (default https://openrouter.ai/api/v1), plus the attribution variables in
`services.openrouter.attribution`.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Iterator

from oc.obs import context, get_logger
from services.cost import Usage

from . import attribution
from .store import CallStore

log = get_logger("openrouter")
DEFAULT_BASE = "https://openrouter.ai/api/v1"
_collector: ContextVar[list | None] = ContextVar("oc_or_usage", default=None)


def base_url() -> str:
    return (os.environ.get("OC_OPENROUTER_BASE_URL") or DEFAULT_BASE).rstrip("/")


class OpenRouterError(RuntimeError):
    def __init__(self, msg: str, *, status: int | None = None, error_class: str = "error"):
        super().__init__(msg)
        self.status, self.error_class = status, error_class


@dataclass
class ChatResult:
    text: str
    usage: Usage | None
    cost: float
    generation_id: str | None
    model_served: str | None
    provider: str | None
    finish_reason: str | None
    latency_ms: int
    cost_source: str
    call_id: str
    raw_usage: dict = field(default_factory=dict)


@contextmanager
def collect_usage() -> Iterator[list[Usage]]:
    """Gather the Usage of every OpenRouter call made inside the block (this thread/context)."""
    box: list[Usage] = []
    token = _collector.set(box)
    try:
        yield box
    finally:
        _collector.reset(token)


def total_usage(items: list[Usage]) -> Usage | None:
    if not items:
        return None
    models = sorted({u.model for u in items if u.model})
    return Usage(
        model=models[0] if len(models) == 1 else ",".join(models) or "unknown",
        tokens_in=sum(u.tokens_in for u in items),
        tokens_out=sum(u.tokens_out for u in items),
        cost=sum(u.cost for u in items),
    )


def _capture_content() -> bool:
    return os.environ.get("OC_OBS_CAPTURE_CONTENT", "").strip().lower() in {"1", "true", "yes", "on"}


def _headers(key: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "User-Agent": "open-computer/0.1 (+https://github.com/duketopceo/open-computer)",
        **({"Authorization": f"Bearer {key}"} if key else {}),
        **attribution.headers(),
    }


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def parse_response(resp: dict) -> dict:
    """Normalize the fields we record from a chat/decisions response. Tolerant of missing pieces."""
    u = resp.get("usage") if isinstance(resp.get("usage"), dict) else {}
    pd = u.get("prompt_tokens_details") if isinstance(u.get("prompt_tokens_details"), dict) else {}
    cd = u.get("completion_tokens_details") if isinstance(u.get("completion_tokens_details"), dict) else {}
    det = u.get("cost_details") if isinstance(u.get("cost_details"), dict) else {}
    cost = _num(u.get("cost"))
    choices = resp.get("choices") if isinstance(resp.get("choices"), list) else []
    first = choices[0] if choices and isinstance(choices[0], dict) else {}
    gid = resp.get("id") if isinstance(resp.get("id"), str) and resp["id"].startswith("gen-") else None
    return {
        "generation_id": gid,
        "model_served": resp.get("model") if isinstance(resp.get("model"), str) else None,
        "provider": resp.get("provider") if isinstance(resp.get("provider"), str) else None,
        "finish_reason": first.get("finish_reason"),
        "tokens_in": int(_num(u.get("prompt_tokens")) or 0),
        "tokens_out": int(_num(u.get("completion_tokens")) or 0),
        "tokens_cached": int(_num(pd.get("cached_tokens")) or 0),
        "tokens_reasoning": int(_num(cd.get("reasoning_tokens")) or 0),
        "cost": cost if cost is not None else 0.0,
        "cost_source": "provider" if cost is not None else "none",
        "upstream_cost": _num(det.get("upstream_inference_cost")),
        "is_byok": u.get("is_byok") if isinstance(u.get("is_byok"), bool) else None,
    }


def _error_class(e: BaseException) -> tuple[str, int | None]:
    if isinstance(e, urllib.error.HTTPError):
        return f"http_{e.code}", e.code
    if isinstance(e, (socket.timeout, TimeoutError)) or isinstance(getattr(e, "reason", None), socket.timeout):
        return "timeout", None
    if isinstance(e, (urllib.error.URLError, OSError)):
        return "network", None
    if isinstance(e, ValueError):
        return "bad_response", None
    return "error", None


def _record(store: CallStore | None, row: dict) -> None:
    try:
        (store or CallStore()).append(row)
    except Exception:
        from oc.obs import log as _l

        _l._count_failure(None, None)


def post(
    url: str,
    payload: dict,
    *,
    key: str = "",
    timeout: float = 60.0,
    kind: str = "chat",
    model: str | None = None,
    body_fields: bool = True,
    store: CallStore | None = None,
) -> tuple[dict, dict]:
    """POST JSON to OpenRouter with attribution; returns (response, call_row). Always records a row."""
    ctx = context.current()
    body = {**(attribution.request_fields(kind, model) if body_fields else {}), **payload}
    data = json.dumps(body).encode()
    call_id = uuid.uuid4().hex[:16]
    base = {
        "event": "call", "call_id": call_id, "kind": kind, "model_requested": model or payload.get("model"),
        "url_host": urllib.parse.urlsplit(url).netloc,
        "prompt_chars": len(json.dumps(payload.get("messages", payload.get("state", "")), default=str)),
        "prompt_sha256": hashlib.sha256(
            json.dumps(payload.get("messages", payload.get("state", "")), default=str, sort_keys=True).encode()
        ).hexdigest()[:16],
        **{k: ctx[k] for k in ("run_id", "subtask", "task", "chat_id", "trace_id", "goal", "service") if k in ctx},
    }
    req = urllib.request.Request(url, data=data, headers=_headers(key), method="POST")
    log.debug("openrouter request", call_id=call_id, kind=kind, model=base["model_requested"], bytes=len(data))
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
        latency = int((time.monotonic() - t0) * 1000)
        resp = json.loads(raw)
        if not isinstance(resp, dict):
            raise ValueError("response is not a JSON object")
    except Exception as e:
        latency = int((time.monotonic() - t0) * 1000)
        klass, status = _error_class(e)
        detail = ""
        if isinstance(e, urllib.error.HTTPError):
            try:
                detail = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
        msg = f"{url} -> HTTP {status}: {detail or getattr(e, 'reason', e)}" if status else f"{url} -> {type(e).__name__}: {e}"
        row = {**base, "status": "error", "error_class": klass, "http_status": status, "latency_ms": latency,
               "error": msg[:300], "cost": 0.0, "cost_source": "none"}
        _record(store, row)
        log.warning("openrouter call failed", call_id=call_id, kind=kind, error_class=klass, http_status=status,
                    latency_ms=latency)
        raise OpenRouterError(msg, status=status, error_class=klass) from e
    parsed = parse_response(resp)
    row = {**base, "status": "ok", "latency_ms": latency, **parsed}
    if _capture_content():
        row["prompt"] = payload.get("messages", payload.get("state"))
        try:
            row["completion"] = resp["choices"][0]["message"]["content"]
        except Exception:
            row["completion"] = None
    _record(store, row)
    (log.info if parsed["cost_source"] == "provider" else log.warning)(
        "openrouter call" if parsed["cost_source"] == "provider" else "openrouter call: no cost reported",
        call_id=call_id, kind=kind, model=parsed["model_served"], provider=parsed["provider"],
        tokens_in=parsed["tokens_in"], tokens_out=parsed["tokens_out"], cost=parsed["cost"], latency_ms=latency,
        generation_id=parsed["generation_id"],
    )
    sink = _collector.get()
    if sink is not None and (parsed["tokens_in"] or parsed["tokens_out"] or parsed["cost_source"] == "provider"):
        sink.append(Usage(parsed["model_served"] or str(base["model_requested"]), parsed["tokens_in"],
                          parsed["tokens_out"], parsed["cost"]))
    row["_raw_usage"] = resp.get("usage") if isinstance(resp.get("usage"), dict) else {}
    return resp, row


def chat(
    model: str,
    prompt: str | None = None,
    *,
    messages: list[dict] | None = None,
    key: str = "",
    timeout: float = 60.0,
    kind: str = "chat",
    store: CallStore | None = None,
    **params,
) -> ChatResult:
    """One chat completion. Raises OpenRouterError (after recording the failed call)."""
    msgs = messages if messages is not None else [{"role": "user", "content": prompt or ""}]
    resp, row = post(f"{base_url()}/chat/completions", {"model": model, "messages": msgs, **params},
                     key=key, timeout=timeout, kind=kind, model=model, store=store)
    try:
        text = resp["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise OpenRouterError(f"unexpected chat response shape (no choices[0].message.content): {e}",
                              error_class="bad_response") from e
    text = text if isinstance(text, str) else ""
    u = None
    if row["tokens_in"] or row["tokens_out"] or row["cost_source"] == "provider":
        u = Usage(row["model_served"] or model, row["tokens_in"], row["tokens_out"], row["cost"])
    return ChatResult(text=text, usage=u, cost=row["cost"], generation_id=row["generation_id"],
                      model_served=row["model_served"], provider=row["provider"], finish_reason=row["finish_reason"],
                      latency_ms=row["latency_ms"], cost_source=row["cost_source"], call_id=row["call_id"],
                      raw_usage=row.get("_raw_usage", {}))
