"""Connect OpenRouter: OAuth PKCE (S256) that mints the user's own API key and keeps it in omaseal.

Flow: `start` makes a verifier + S256 challenge + one-shot state, kept in memory here (never sent to the browser)
and returns OpenRouter's sign-in URL; OpenRouter redirects back to `<origin>/settings?oc_state=<state>&code=...`;
the console posts the code to `complete`, which exchanges it with OpenRouter's official Python SDK and stores the
key in omaseal (`openrouter/open-computer`), the same keyring the host already uses for secrets. The key is never
returned to the browser, never written to a file, never logged. This process also exports it as
OPENROUTER_API_KEY when none is set, so it can spend straight away; other oc-* services read the key from their
own environment (injected from omaseal) and pick it up on their next restart.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import subprocess
import threading
import time
from urllib.parse import urlencode, urlsplit

from oc.obs import error_fields, get_logger

from . import auth

log = get_logger("opsview")
SERVICE, ACCOUNT = "openrouter", "open-computer"
AUTH_URL = "https://openrouter.ai/auth"
STATE_TTL = 600.0
_MAX_PENDING = 16
_pending: dict[str, tuple[str, str, float]] = {}  # state -> (verifier, callback_url, expires)
_lock = threading.Lock()


class ConnectError(Exception):
    """A user-presentable failure (the message never contains a key or code)."""


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _stored() -> bool:
    """Is our entry in omaseal? Lists metadata only; the secret value is never read to answer this."""
    try:
        p = subprocess.run(["omaseal", "ls", SERVICE], capture_output=True, text=True, timeout=4)
    except Exception:
        return False
    return p.returncode == 0 and ACCOUNT in p.stdout


def status() -> dict:
    """{connected, source}: `omaseal` when the user connected here, `env` for a deployment-provided key."""
    if _stored():
        return {"connected": True, "source": "omaseal"}
    if os.environ.get("OPENROUTER_API_KEY"):
        return {"connected": True, "source": "env"}
    return {"connected": False, "source": None}


def start(origin: str, host: str) -> dict:
    """Begin a connect. `origin` must be the console's own origin (the callback comes back to it)."""
    parts = urlsplit(origin)
    if parts.scheme not in ("http", "https") or parts.netloc != host:
        raise ConnectError("Open the console from its own address to connect OpenRouter.")
    verifier = secrets.token_urlsafe(48)  # 64 chars, RFC 7636 allows 43-128
    state = secrets.token_urlsafe(24)
    callback = f"{origin}/settings?" + urlencode({"oc_state": state})
    now = time.monotonic()
    with _lock:
        for k in [k for k, v in _pending.items() if v[2] < now]:
            del _pending[k]
        while len(_pending) >= _MAX_PENDING:
            del _pending[min(_pending, key=lambda k: _pending[k][2])]
        _pending[state] = (verifier, callback, now + STATE_TTL)
    challenge = _b64(hashlib.sha256(verifier.encode()).digest())
    url = AUTH_URL + "?" + urlencode(
        {"callback_url": callback, "code_challenge": challenge, "code_challenge_method": "S256"}
    )
    return {"url": url}


def _exchange(code: str, verifier: str) -> str:
    from openrouter import OpenRouter

    from services.openrouter import attribution, client

    with OpenRouter(server_url=client.base_url()) as sdk:
        res = sdk.o_auth.exchange_auth_code_for_api_key(
            code=code, code_verifier=verifier, code_challenge_method="S256", retries=None, timeout_ms=15000,
            http_headers=attribution.headers(),
        )
    return res.key


def complete(code: str, state: str) -> dict:
    code, state = str(code or "").strip(), str(state or "").strip()
    if not code or not state or len(code) > 512:
        raise ConnectError("The sign-in response was incomplete. Try connecting again.")
    with _lock:
        row = _pending.pop(state, None)  # one shot: a replayed state finds nothing
    if row is None or row[2] < time.monotonic():
        raise ConnectError("That sign-in expired. Try connecting again.")
    try:
        key = _exchange(code, row[0])
    except Exception as e:  # noqa: BLE001 - the SDK error text can echo request bodies; log the class only
        log.warning("openrouter connect exchange failed", **error_fields(e))
        raise ConnectError("OpenRouter did not accept the sign-in. Try connecting again.") from e
    if not key or not auth._omaseal_set(SERVICE, ACCOUNT, key):
        raise ConnectError("Connected, but the key could not be saved to omaseal (is the session unlocked?).")
    os.environ.setdefault("OPENROUTER_API_KEY", key)
    log.info("openrouter connected", source="oauth_pkce")
    return {"connected": True, "source": "omaseal"}


def disconnect() -> dict:
    """Delete the stored key. A key injected through the deployment environment is not ours to remove."""
    try:
        p = subprocess.run(["omaseal", "rm", SERVICE, ACCOUNT], capture_output=True, text=True, timeout=4)
        ok = p.returncode == 0
    except Exception:
        ok = False
    if not ok and _stored():
        raise ConnectError("Could not remove the key from omaseal.")
    log.info("openrouter disconnected")
    return status()
