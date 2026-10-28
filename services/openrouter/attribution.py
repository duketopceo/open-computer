"""OpenRouter app attribution + per-request tags, applied to EVERY call from one place.

What it enables (OpenRouter docs, checked 2026-10-28): `HTTP-Referer` (+ title) creates an app so usage can be
filtered/grouped by app in Activity and the analytics API; `session_id` groups generations into a chat; `user`
fills `external_user`; the free-form `trace` object is passed to every Broadcast destination.

Env (all optional):
  OC_OPENROUTER_ATTRIBUTION   on (default) | off — off sends no attribution headers and no `user`
  OC_OPENROUTER_REFERER       app identity URL (default: this project's public repo)
  OC_OPENROUTER_TITLE         display name (default: Open Computer)
  OC_OPENROUTER_CATEGORIES    comma list, max 2, from OpenRouter's documented categories
  OC_OPENROUTER_APP_VISIBILITY  `hidden` keeps a newly created app out of public rankings
  OC_OPENROUTER_USER          opaque id; sent only as a truncated sha256, never raw
  OC_OPENROUTER_ENVIRONMENT   free-form, e.g. prod/dev, sent as trace.environment
"""

from __future__ import annotations

import hashlib
import os

from oc.obs import context

DEFAULT_REFERER = "https://github.com/duketopceo/open-computer"
DEFAULT_TITLE = "Open Computer"
CATEGORIES = {
    "cli-agent", "ide-extension", "cloud-agent", "programming-app", "native-app-builder",
    "creative-writing", "video-gen", "image-gen", "audio-gen",
    "writing-assistant", "general-chat", "personal-agent", "legal", "roleplay", "game",
}


def enabled() -> bool:
    return os.environ.get("OC_OPENROUTER_ATTRIBUTION", "on").strip().lower() not in {"off", "0", "false", "no"}


def referer() -> str:
    return os.environ.get("OC_OPENROUTER_REFERER") or DEFAULT_REFERER


def title() -> str:
    return os.environ.get("OC_OPENROUTER_TITLE") or DEFAULT_TITLE


def categories() -> list[str]:
    raw = [c.strip().lower() for c in os.environ.get("OC_OPENROUTER_CATEGORIES", "").split(",")]
    return [c for c in raw if c in CATEGORIES][:2]


def headers() -> dict[str, str]:
    """Attribution headers. Empty when attribution is off."""
    if not enabled():
        return {}
    h = {
        "HTTP-Referer": referer(),
        "X-OpenRouter-Title": title(),
        "X-Title": title(),  # older spelling, still accepted
    }
    cats = categories()
    if cats:
        h["X-OpenRouter-Categories"] = ",".join(cats)
    if os.environ.get("OC_OPENROUTER_APP_VISIBILITY", "").strip().lower() == "hidden":
        h["X-OpenRouter-App-Visibility"] = "hidden"
    return h


def user_field() -> str | None:
    raw = os.environ.get("OC_OPENROUTER_USER", "")
    if not enabled() or not raw:
        return None
    return "oc_" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def request_fields(kind: str = "chat", model: str | None = None) -> dict:
    """Body fields for the current context: session_id (chat), user, trace{...}. {} when off.

    Only OpenRouter-documented fields plus namespaced `oc_*` metadata keys are sent."""
    if not enabled():
        return {}
    ctx = context.current()
    out: dict = {}
    if ctx.get("chat_id"):
        out["session_id"] = ctx["chat_id"][:256]
    u = user_field()
    if u:
        out["user"] = u
    trace = {"trace_name": f"oc.{kind}", "generation_name": f"{kind}:{model or 'model'}"}
    if ctx.get("trace_id"):
        trace["trace_id"] = ctx["trace_id"]
    if ctx.get("subtask"):
        trace["span_name"] = f"subtask:{ctx['subtask']}"
    env = os.environ.get("OC_OPENROUTER_ENVIRONMENT")
    if env:
        trace["environment"] = env
    for src, dst in (("run_id", "oc_run"), ("subtask", "oc_subtask"), ("chat_id", "oc_chat"), ("goal", "oc_goal")):
        if ctx.get(src):
            trace[dst] = ctx[src]
    out["trace"] = trace
    return out
