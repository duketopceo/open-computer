"""Composio connectors: each account connects its own apps through Composio-managed OAuth.

Inert unless `COMPOSIO_API_KEY` is set. The key is read from the environment on each call, never logged and never
returned. The SDK is imported only when the key is set. The Composio user id is the OC account id
(services.accounts.account_id), never an email.

Connecting goes through a session: `composio.create(user_id=...)` then `session.authorize(toolkit, callback_url=...)`
returns a hosted Connect Link. OC never sees provider tokens.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from typing import Any

from oc.obs import get_logger

log = get_logger("connectors")

DEFAULT_TOOLKITS = ("gmail", "googlecalendar", "googledrive", "github", "slack", "notion")
LABELS = {
    "gmail": "Gmail",
    "googlecalendar": "Google Calendar",
    "googledrive": "Google Drive",
    "github": "GitHub",
    "slack": "Slack",
    "notion": "Notion",
}
_SLUG = re.compile(r"[a-z0-9_]{2,40}")


class ConnectorError(Exception):
    """A user-facing failure. The message never carries the key or a raw SDK error."""


def enabled() -> bool:
    return bool(os.environ.get("COMPOSIO_API_KEY", "").strip())


def toolkits() -> list[str]:
    """The curated toolkits users may connect: `OC_COMPOSIO_TOOLKITS` (comma-separated slugs) or the default set."""
    raw = os.environ.get("OC_COMPOSIO_TOOLKITS", "")
    picked = [s.strip().lower() for s in raw.split(",") if s.strip()] if raw.strip() else list(DEFAULT_TOOLKITS)
    return [s for s in dict.fromkeys(picked) if _SLUG.fullmatch(s)]


def _default_client() -> Any:
    from composio import Composio

    return Composio(api_key=os.environ["COMPOSIO_API_KEY"].strip())


# Tests replace this with a fake; production builds the real SDK client per call.
client_factory: Callable[[], Any] = _default_client


def _client() -> Any:
    if not enabled():
        raise ConnectorError("Connectors are off. Set COMPOSIO_API_KEY on the host to turn them on.")
    return client_factory()


def _call(what: str, fn: Callable[[], Any]) -> Any:
    try:
        return fn()
    except ConnectorError:
        raise
    except Exception as e:  # the SDK raises many types; the user gets one plain message, the log gets the type
        log.warning("composio call failed", call=what, error=type(e).__name__)
        raise ConnectorError(f"Composio could not {what} right now. Try again in a minute.") from None


def _active(c: Any, user_id: str) -> list[dict]:
    res = _call("list your connections", lambda: c.connected_accounts.list(user_ids=[user_id], statuses=["ACTIVE"]))
    out = []
    for it in getattr(res, "items", None) or []:
        if getattr(it, "user_id", user_id) != user_id:
            continue  # defence in depth: only this user's accounts, whatever the filter returned
        slug = getattr(getattr(it, "toolkit", None), "slug", "") or ""
        out.append({"id": str(it.id), "toolkit": slug.lower(), "status": str(getattr(it, "status", "ACTIVE"))})
    return out


def status(user_id: str) -> dict:
    """What the Connectors section shows: whether Composio is on, and each curated toolkit with its connection."""
    if not enabled():
        return {"enabled": False, "toolkits": []}
    conns = _active(_client(), user_id)
    rows = []
    for slug in toolkits():
        mine = [c for c in conns if c["toolkit"] == slug]
        rows.append({
            "slug": slug,
            "label": LABELS.get(slug, slug.replace("_", " ").title()),
            "connected": bool(mine),
            "account": mine[0]["id"] if mine else None,
        })
    return {"enabled": True, "toolkits": rows}


def connect(user_id: str, toolkit: object, callback_url: str) -> dict:
    """Start connecting `toolkit` for this user: returns the Composio Connect Link to open."""
    if not isinstance(toolkit, str) or toolkit not in toolkits():
        raise ConnectorError("That app is not on this computer's list.")
    c = _client()
    session = _call("start a connection", lambda: c.create(user_id=user_id, toolkits=[toolkit]))
    req = _call("start a connection", lambda: session.authorize(toolkit, callback_url=callback_url))
    url = getattr(req, "redirect_url", None)
    if not isinstance(url, str) or not url.startswith("https://"):
        raise ConnectorError("Composio did not return a sign-in link. Try again in a minute.")
    log.info("connector link created", toolkit=toolkit)
    return {"url": url}


def disconnect(user_id: str, account: object) -> dict:
    """Remove one of this user's connections. Refuses an id the user does not own."""
    c = _client()
    if not isinstance(account, str) or account not in {a["id"] for a in _active(c, user_id)}:
        raise ConnectorError("That connection is not yours or is already gone.")
    _call("remove the connection", lambda: c.connected_accounts.delete(account))
    log.info("connector removed")
    return {"ok": True}
