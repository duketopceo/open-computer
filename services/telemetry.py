"""Best-effort failure telemetry: Glitchtip (Sentry envelope) + tallyline intake.

Both sinks are optional and env-gated — unset means silent no-op, a broken sink
never breaks the caller. Stdlib only (urllib), ~2s timeouts.

Env:
  OC_SENTRY_DSN          Sentry-compatible DSN (Glitchtip project key)
  OC_TALLYLINE_URL       e.g. https://api.tallyline.app
  OC_TALLYLINE_WORKSPACE workspace slug (e.g. luke)
  OC_TALLYLINE_KEY       agent key bearer token
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
import traceback
import urllib.request
import uuid
from datetime import datetime, timezone

from oc.obs import get_logger

log = get_logger("telemetry")

_HOST = socket.gethostname()
_TIMEOUT = 2.0


def _sentry_env():
    return os.environ.get("OC_SENTRY_DSN")


def _tallyline_env():
    url = os.environ.get("OC_TALLYLINE_URL", "").rstrip("/")
    ws = os.environ.get("OC_TALLYLINE_WORKSPACE", "")
    key = os.environ.get("OC_TALLYLINE_KEY", "")
    return (url, ws, key) if url and ws and key else None


def _post(url, headers, payload: bytes) -> None:
    sink = "sentry" if "X-Sentry-Auth" in headers else "tallyline"
    # Cloudflare's bot check 403s the bare urllib UA (error 1010)
    req = urllib.request.Request(url, data=payload, method="POST", headers={
        "User-Agent": "oc-telemetry/0.1 (+open-computer)", **headers,
    })
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            status = getattr(r, "status", None)
        log.debug("telemetry sent", sink=sink, status=status, ms=round((time.monotonic() - t0) * 1000, 1),
                  bytes=len(payload))
    except Exception as e:  # never propagates; the failure is logged, never the URL or credentials
        log.warning("telemetry send failed", sink=sink, error=type(e).__name__,
                    ms=round((time.monotonic() - t0) * 1000, 1))


def _send_sentry(title: str, body: str | None, level: str, fingerprint: str | None,
                 extra: dict | None, exc=None) -> None:
    dsn = _sentry_env()
    if not dsn:
        return
    # dsn: https://<key>@<host>/<project>
    try:
        key, rest = dsn.removeprefix("https://").split("@", 1)
        host, project = rest.split("/", 1)
    except ValueError:
        return
    event = {
        "event_id": uuid.uuid4().hex,
        "platform": "python",
        "level": level,
        "message": title,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "server_name": _HOST,
        "tags": {"service": "open-computer"},
        "extra": {**(extra or {}), **({"detail": body} if body else {})},
    }
    if fingerprint:
        event["fingerprint"] = [fingerprint]
    if exc is not None:
        event["exception"] = {"values": [{
            "type": type(exc).__name__,
            "value": str(exc),
            "stacktrace": {"frames": [
                {"filename": f.filename, "lineno": f.lineno, "function": f.name}
                for f in traceback.extract_tb(exc.__traceback__)
            ]},
        }]}
    envelope = (
        json.dumps({"event_id": event["event_id"], "dsn": dsn}) + "\n"
        + json.dumps({"type": "event"}) + "\n"
        + json.dumps(event)
    ).encode()
    _post(
        f"https://{host}/api/{project}/envelope/",
        {"X-Sentry-Auth": f"Sentry sentry_version=7, sentry_key={key}, sentry_client=oc-telemetry/0.1",
         "Content-Type": "application/x-sentry-envelope"},
        envelope,
    )


def _send_tallyline(title: str, body: str | None, severity: str, fingerprint: str | None,
                    extra: dict | None) -> None:
    cfg = _tallyline_env()
    if not cfg:
        return
    url, ws, key = cfg
    # zod .optional() means absent, not null — omit empty fields entirely
    ev: dict = {
        "kind": "error",
        "title": title[:500],
        "provider": "open-computer",
        "severity": {"fatal": "fatal", "error": "error", "warning": "warning"}.get(severity, "error"),
        "occurredAt": datetime.now(timezone.utc).isoformat(),
        "payload": {"host": _HOST, **(extra or {})},
    }
    if body:
        ev["body"] = body[:100_000]
    if fingerprint:
        ev["fingerprint"] = fingerprint
    payload = json.dumps(ev).encode()
    _post(
        f"{url}/v1/workspaces/{ws}/intake",
        {"Authorization": f"Bearer {key}", "X-Actor-Type": "agent",
         "Content-Type": "application/json"},
        payload,
    )


def report_failure(title: str, *, body: str | None = None, severity: str = "error",
                   fingerprint: str | None = None, exc: BaseException | None = None,
                   **extra) -> None:
    """Report a failure to every configured sink. Never raises."""
    try:
        log.debug("report", severity=severity, fingerprint=fingerprint)
        _send_sentry(title, body, severity, fingerprint, extra, exc=exc)
        _send_tallyline(title, body, severity, fingerprint, extra)
    except Exception as e:
        log.warning("telemetry report failed", error=type(e).__name__)


def install_excepthook() -> None:
    """Report uncaught exceptions, then defer to the default hook."""
    prev = sys.excepthook

    def hook(exc_type, exc, tb):
        report_failure(f"uncaught {exc_type.__name__}: {exc}", exc=exc)
        prev(exc_type, exc, tb)

    sys.excepthook = hook
