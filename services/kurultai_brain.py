"""Link to Kurultai's Brain UI from Open Computer Settings (HTTP only; AGPL stays out of this repo).

Set OC_KURULTAI_BRAIN_URL to the Brain UI base (e.g. http://127.0.0.1:8421/ui/). When unset the
Settings section is inert. Health is a short GET against that URL (or OC_KURULTAI_BRAIN_HEALTH_URL
when set); failures are reported, never raised into the console request path.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit


def _url(env: dict[str, str] | None = None) -> str:
    e = os.environ if env is None else env
    return (e.get("OC_KURULTAI_BRAIN_URL") or "").strip()


def _health_url(brain: str, env: dict[str, str] | None = None) -> str:
    e = os.environ if env is None else env
    override = (e.get("OC_KURULTAI_BRAIN_HEALTH_URL") or "").strip()
    if override:
        return override
    # Prefer the daemon root: /ui/ may be a SPA that always 200s; /api/status is Kurultai's check.
    parts = urlsplit(brain)
    if not parts.scheme or not parts.netloc:
        return brain
    return f"{parts.scheme}://{parts.netloc}/api/status"


def _probe(url: str, opener: Callable = urllib.request.urlopen, timeout: float = 2.0) -> str:
    if not url:
        return "unset"
    try:
        req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json, text/html, */*"})
        with opener(req, timeout=timeout) as r:
            code = getattr(r, "status", None) or r.getcode()
        return "up" if 200 <= int(code) < 500 else "down"
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return "down"


def status(env: dict[str, str] | None = None, opener: Callable | None = None) -> dict[str, Any]:
    """Settings payload: URL (or null), health (unset|up|down). Never raises."""
    brain = _url(env)
    if not brain:
        return {"url": None, "health": "unset", "hint": "Set OC_KURULTAI_BRAIN_URL to link Kurultai's Brain UI (e.g. http://127.0.0.1:8421/ui/)."}
    health = _probe(_health_url(brain, env), opener=opener or urllib.request.urlopen)
    return {"url": brain, "health": health, "hint": None}
