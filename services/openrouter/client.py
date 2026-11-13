"""The one OpenRouter HTTP client: attribution on every call, a complete local record of every request.

Provider-reported `usage.cost` is the cost truth (OpenRouter always returns it; no price table here). A call
with no reported cost is recorded `cost_source: "none"` and warned, never guessed. Recording failures never
affect the call. Prompt/completion text is stored only with OC_OBS_CAPTURE_CONTENT=1 (and still redacted).

Every chat call carries the model's catalog fallback chain as OpenRouter's native `models` array
(services/router/model_catalog.py `call_params`); the row records the chain asked (`models_requested`), the model
that served (`model_served`, from the response) and `fell_back` when that is not the first choice. If the whole
request still fails with a region, no-endpoint, 429 or 5xx error, `chat` retries with the remaining tail.

Transport: chat completions go through OpenRouter's official Python SDK (`openrouter`, pinned in pyproject); the
SDK owns auth, attribution headers, timeouts and HTTP error typing. A response-capture hook keeps the raw body,
because the SDK's typed result drops `provider` and `cost_details` (both recorded here) and rejects bodies that
lack optional-in-practice fields. The SDK has no `/alpha/decisions` endpoint, so that one call (`post` without
`sdk=True`) stays on urllib.

Env: OC_OPENROUTER_BASE_URL (default https://openrouter.ai/api/v1), plus the attribution variables in
`services.openrouter.attribution`. Keys and priority per lane (chat, subagents, background): `services.openrouter.lanes`.
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

import httpx
from openrouter import OpenRouter
from openrouter import errors as or_errors
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Iterator

from oc.obs import capture_content, context, devtrace, error_fields, get_logger, redact_str
from services.cost import Usage

from . import attribution, lanes
from .store import CallStore

log = get_logger("openrouter")
DEFAULT_BASE = "https://openrouter.ai/api/v1"
_collector: ContextVar[list | None] = ContextVar("oc_or_usage", default=None)
_reasoning: ContextVar[list | None] = ContextVar("oc_or_reasoning", default=None)
_effort: ContextVar[str | None] = ContextVar("oc_or_effort", default=None)


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


@contextmanager
def effort_scope(effort: str | None) -> Iterator[None]:
    """Every chat call inside the block runs at this effort level (low/medium/high) unless it names its own."""
    token = _effort.set(effort)
    try:
        yield
    finally:
        _effort.reset(token)


@contextmanager
def collect_reasoning() -> Iterator[list[str]]:
    """Gather the reasoning text models returned on every OpenRouter call made inside the block.

    Collects whatever the model sends back; `chat` asks for reasoning only on Medium/High effort."""
    box: list[str] = []
    token = _reasoning.set(box)
    try:
        yield box
    finally:
        _reasoning.reset(token)


_DETAIL_FIELD = {"reasoning.text": "text", "reasoning.summary": "summary"}


def extract_reasoning(resp: dict) -> str:
    """`message.reasoning`, else the text/summary entries of `message.reasoning_details`; encrypted entries are
    opaque and skipped."""
    try:
        msg = resp["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return ""
    if not isinstance(msg, dict):
        return ""
    if isinstance(msg.get("reasoning"), str) and msg["reasoning"].strip():
        return msg["reasoning"]
    parts = []
    for d in msg.get("reasoning_details") or []:
        if not isinstance(d, dict):
            continue
        v = d.get(_DETAIL_FIELD.get(d.get("type"), ""))
        if isinstance(v, str) and v.strip():
            parts.append(v)
    return "\n\n".join(parts)


def total_usage(items: list[Usage]) -> Usage | None:
    """Sum a step's calls. The first served model and provider win (a step's calls share both in practice)."""
    if not items:
        return None
    return Usage(
        model=next((u.model for u in items if u.model), "unknown"),
        tokens_in=sum(u.tokens_in for u in items),
        tokens_out=sum(u.tokens_out for u in items),
        cost=sum(u.cost for u in items),
        provider=next((u.provider for u in items if u.provider), None),
        tokens_reasoning=sum(u.tokens_reasoning for u in items),
    )


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
    upstream = _num(det.get("upstream_inference_cost"))
    # BYOK: `usage.cost` is only OpenRouter's fee (often 0); the provider's real charge is upstream.
    byok_upstream = u.get("is_byok") is True and upstream is not None and cost is not None
    if byok_upstream:
        cost += upstream
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
        "upstream_cost": upstream,
        "is_byok": u.get("is_byok") if isinstance(u.get("is_byok"), bool) else None,
        **({"cost_byok_upstream": True} if byok_upstream else {}),
    }


def _fallback_fields(base: dict, parsed: dict) -> dict:
    """`fell_back: true` when a fallback chain was sent and a later model served (OpenRouter may append a dated
    suffix to the served id, so a served id that starts with the first choice is still the first choice)."""
    chain, served = base.get("models_requested"), parsed.get("model_served")
    if not chain or not served:
        return {}
    first = str(chain[0])
    fell = not (served == first or served.startswith(first + "-") or served.startswith(first + ":"))
    if fell:
        log.warning("openrouter fallback served the call", requested=first, served=served)
    return {"fell_back": fell}


def _error_class(e: BaseException) -> tuple[str, int | None]:
    if isinstance(e, lanes.SlotTimeout):
        return "queue_timeout", None
    if isinstance(e, or_errors.OpenRouterError):
        if e.status_code < 400:  # a 2xx the SDK could not parse (e.g. an HTML page): not an HTTP failure
            return "bad_response", None
        return f"http_{e.status_code}", e.status_code
    if isinstance(e, (or_errors.ResponseValidationError, json.JSONDecodeError)):
        return "bad_response", None
    if isinstance(e, httpx.TimeoutException):
        return "timeout", None
    if isinstance(e, (httpx.TransportError, httpx.InvalidURL)):
        return "network", None
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


def _trace_call(ctx: dict, row: dict, resp: dict | None = None) -> None:
    """Dev trace: one `llm_call` per request, for runs that have a stream. Never affects the call."""
    trace = devtrace.tracer(run_id=ctx.get("run_id"))
    if trace is None:
        return
    try:
        subtask = ctx.get("subtask")
        kind = row["kind"]
        if kind == "chat":  # the same client serves the planner, the workers and chat turns
            kind = "work" if subtask else "plan" if ctx.get("service") == "orchestrator" else "chat"
        ok = row["status"] == "ok"
        reasoning = extract_reasoning(resp) if resp is not None else ""
        trace.emit(
            "llm_call",
            id=devtrace.entity("call", row["call_id"]),
            kind=kind,
            status=row["status"],
            step=devtrace.entity("step", subtask) if subtask else None,
            model_requested=row.get("model_requested"),
            model_served=row.get("model_served"),
            provider=row.get("provider"),
            tokens_in=row.get("tokens_in"),
            tokens_out=row.get("tokens_out"),
            tokens_cached=row.get("tokens_cached"),
            tokens_reasoning=row.get("tokens_reasoning"),
            cost_usd=max(0.0, row["cost"]) if ok else None,
            latency_ms=row.get("latency_ms"),
            generation_id=row.get("generation_id"),
            reasoning=devtrace.text(reasoning, devtrace.REASONING_CAP) if reasoning else None,
            error_class=row.get("error_class"),
            http_status=row.get("http_status"),
            error=devtrace.text(row["error"], 300) if row.get("error") else None,
        )
    except Exception as e:  # noqa: BLE001 - tracing never changes a call
        log.warning("dev trace call dropped", **error_fields(e))


def _urllib_send(url: str, data: bytes, key: str, timeout: float) -> dict:
    req = urllib.request.Request(url, data=data, headers=_headers(key), method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    resp = json.loads(raw)
    if not isinstance(resp, dict):
        raise ValueError("response is not a JSON object")
    return resp


def _sdk_send(body: dict, key: str, timeout: float) -> dict:
    """POST /chat/completions through the official SDK; returns the raw response body as a dict."""
    kw = dict(body)
    messages = kw.pop("messages")
    captured: list[bytes] = []

    def _grab(r: httpx.Response) -> None:
        r.read()
        captured.append(r.content)

    hdrs = {k: v for k, v in _headers("").items() if k.lower() not in ("content-type", "authorization")}
    with httpx.Client(event_hooks={"response": [_grab]}) as hc:
        client = OpenRouter(api_key=key or None, server_url=base_url(), client=hc)
        try:
            client.chat.send(messages=messages, timeout_ms=int(timeout * 1000), http_headers=hdrs,
                             retries=None, **kw)  # no SDK retries: the SDK default is ~1h of 5XX backoff
        except or_errors.ResponseValidationError:
            # A 2xx whose shape the typed model rejects: the raw body is still the truth we record.
            if not captured:
                raise
    raw = json.loads(captured[-1]) if captured else None
    if not isinstance(raw, dict):
        raise ValueError("response is not a JSON object")
    return raw


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
    sdk: bool = False,
    models_requested: list[str] | None = None,
) -> tuple[dict, dict]:
    """POST JSON to OpenRouter with attribution; returns (response, call_row). Always records a row.
    `sdk=True` sends through the official SDK (chat completions); otherwise urllib (`/alpha/decisions`).
    `models_requested` is what the call log records as the asked chain (defaults to `payload["models"]`)."""
    ctx = context.current()
    lane = lanes.current()
    key = lanes.resolve(key, lane)  # the lane's own key when it has one (OPENROUTER_API_KEY_<LANE>)
    body = {**(attribution.request_fields(kind, model) if body_fields else {}), **payload}
    data = json.dumps(body).encode()
    call_id = uuid.uuid4().hex[:16]
    base = {
        "event": "call", "call_id": call_id, "kind": kind, "lane": lane, "model_requested": model or payload.get("model"),
        **({"models_requested": list(models_requested if models_requested is not None else payload["models"])}
           if isinstance(models_requested if models_requested is not None else payload.get("models"), list) else {}),
        "url_host": urllib.parse.urlsplit(url).netloc,
        "prompt_chars": len(json.dumps(payload.get("messages", payload.get("state", "")), default=str)),
        "prompt_sha256": hashlib.sha256(
            json.dumps(payload.get("messages", payload.get("state", "")), default=str, sort_keys=True).encode()
        ).hexdigest()[:16],
        **{k: ctx[k] for k in ("run_id", "subtask", "task", "chat_id", "trace_id", "goal", "service") if k in ctx},
    }
    log.debug("openrouter request", call_id=call_id, kind=kind, model=base["model_requested"], bytes=len(data))
    t0 = time.monotonic()
    try:
        with lanes.SCHEDULER.slot(lane, wait=timeout):  # chat first when calls queue; bounded wait; see lanes.py
            resp = _sdk_send(body, key, timeout) if sdk else _urllib_send(url, data, key, timeout)
        latency = int((time.monotonic() - t0) * 1000)
    except Exception as e:
        latency = int((time.monotonic() - t0) * 1000)
        klass, status = _error_class(e)
        detail = ""
        if isinstance(e, urllib.error.HTTPError):
            try:
                detail = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
        elif isinstance(e, or_errors.OpenRouterError):
            detail = str(getattr(e, "body", "") or "")[:300]
        msg = f"{url} -> HTTP {status}: {detail or getattr(e, 'reason', e)}" if status else f"{url} -> {type(e).__name__}: {e}"
        msg = redact_str(msg)  # provider error bodies can echo credentials; never let them reach logs or audit
        row = {**base, "status": "error", "error_class": klass, "http_status": status, "latency_ms": latency,
               "error": msg[:300], "cost": 0.0, "cost_source": "none"}
        _record(store, row)
        _trace_call(ctx, row)
        log.warning("openrouter call failed", call_id=call_id, kind=kind, error_class=klass, http_status=status,
                    latency_ms=latency)
        raise OpenRouterError(msg, status=status, error_class=klass) from e
    parsed = parse_response(resp)
    parsed.update(_fallback_fields(base, parsed))
    row = {**base, "status": "ok", "latency_ms": latency, **parsed}
    if capture_content():
        row["prompt"] = payload.get("messages", payload.get("state"))
        try:
            row["completion"] = resp["choices"][0]["message"]["content"]
        except Exception:
            row["completion"] = None
    _record(store, row)
    _trace_call(ctx, row, resp)
    (log.info if parsed["cost_source"] == "provider" else log.warning)(
        "openrouter call" if parsed["cost_source"] == "provider" else "openrouter call: no cost reported",
        call_id=call_id, kind=kind, model=parsed["model_served"], provider=parsed["provider"],
        tokens_in=parsed["tokens_in"], tokens_out=parsed["tokens_out"], cost=parsed["cost"], latency_ms=latency,
        generation_id=parsed["generation_id"],
    )
    sink = _collector.get()
    if sink is not None and (parsed["tokens_in"] or parsed["tokens_out"] or parsed["cost_source"] == "provider"):
        sink.append(Usage(parsed["model_served"] or str(base["model_requested"]), parsed["tokens_in"],
                          parsed["tokens_out"], parsed["cost"], parsed["provider"], parsed["tokens_reasoning"]))
    thoughts = _reasoning.get()
    if thoughts is not None:
        text = extract_reasoning(resp)
        if text:
            thoughts.append(text)
    row["_raw_usage"] = resp.get("usage") if isinstance(resp.get("usage"), dict) else {}
    return resp, row


_FAILOVER_HTTP = frozenset({429, 500, 502, 503, 504})


def is_failover_error(err: OpenRouterError) -> bool:
    """True when a failed request should be retried on the next model in the catalog chain.

    OpenRouter's native `models` array already tries the chain inside one request. The client walks
    the remaining tail when that whole request still fails: a 404, a 400 whose body says there are
    no endpoints (including a data-region miss), a 429, or a 5xx. Other 400s are not retried, because
    they are usually a bad request the next model would also reject.
    """
    if err.status in _FAILOVER_HTTP or err.status == 404:
        return True
    if err.status == 400:
        msg = str(err).lower()
        return "no endpoints" in msg or "data region" in msg
    return False


def _attempt_bodies(model: str, body: dict) -> list[tuple[list[str], dict]]:
    """`(remaining_chain, request_body)` pairs: the full chain first, then each tail."""
    raw = body.get("models")
    has_models = isinstance(raw, list) and bool(raw)
    chain = list(raw) if has_models else [model]
    attempts: list[tuple[list[str], dict]] = []
    for i in range(len(chain)):
        remaining = chain[i:]
        attempt = {k: v for k, v in body.items() if k not in ("model", "models")}
        if len(remaining) > 1:
            attempt["models"] = remaining
        else:
            attempt["model"] = remaining[0]
            if has_models and len(chain) == 1:
                attempt["models"] = remaining  # explicit single-item `models`: keep both fields
        attempts.append((remaining, attempt))
    return attempts


def chat(
    model: str,
    prompt: str | None = None,
    *,
    messages: list[dict] | None = None,
    key: str = "",
    timeout: float = 60.0,
    kind: str = "chat",
    store: CallStore | None = None,
    effort: str | None = None,
    **params,
) -> ChatResult:
    """One chat completion. Raises OpenRouterError (after recording the failed call).

    A catalog model is sent as its fallback chain (`models`, no `model`: OpenRouter tries them in
    order) and, on Medium/High `effort` for a reasoning model, with `reasoning`. Explicit `params`
    win over both. If the whole request still fails with a region, no-endpoint, 429 or 5xx error,
    the client retries with the remaining tail of the chain and records which model served.
    """
    from services.router.model_catalog import call_params  # local: the router package imports this client

    msgs = messages if messages is not None else [{"role": "user", "content": prompt or ""}]
    extra = {**call_params(model, effort if effort is not None else _effort.get()), **params}
    body = {"model": model, "messages": msgs, **extra}
    raw = body.get("models")
    chain = list(raw) if isinstance(raw, list) and raw else [model]
    record_chain = chain if len(chain) > 1 else None
    last_err: OpenRouterError | None = None
    for remaining, attempt in _attempt_bodies(model, body):
        try:
            resp, row = post(
                f"{base_url()}/chat/completions",
                attempt,
                key=key,
                timeout=timeout,
                kind=kind,
                model=model,
                store=store,
                sdk=True,
                models_requested=record_chain,
            )
        except OpenRouterError as e:
            last_err = e
            nxt = remaining[1] if len(remaining) > 1 else None
            if nxt is None or not is_failover_error(e):
                raise
            log.warning(
                "openrouter call failed, trying next fallback",
                requested=model,
                failed=remaining[0],
                next=nxt,
                error_class=e.error_class,
                http_status=e.status,
            )
            continue
        try:
            text = resp["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise OpenRouterError(f"unexpected chat response shape (no choices[0].message.content): {e}",
                                  error_class="bad_response") from e
        text = text if isinstance(text, str) else ""
        u = None
        if row["tokens_in"] or row["tokens_out"] or row["cost_source"] == "provider":
            u = Usage(row["model_served"] or model, row["tokens_in"], row["tokens_out"], row["cost"], row["provider"],
                      row["tokens_reasoning"])
        return ChatResult(text=text, usage=u, cost=row["cost"], generation_id=row["generation_id"],
                          model_served=row["model_served"], provider=row["provider"], finish_reason=row["finish_reason"],
                          latency_ms=row["latency_ms"], cost_source=row["cost_source"], call_id=row["call_id"],
                          raw_usage=row.get("_raw_usage", {}))
    assert last_err is not None
    raise last_err
