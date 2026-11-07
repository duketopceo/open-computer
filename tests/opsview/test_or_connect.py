"""Connect OpenRouter (OAuth PKCE): mocks only, no live OpenRouter or omaseal."""

from __future__ import annotations

import base64
import hashlib
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from services.opsview import or_connect

KEY = "sk-or-v1-" + "a1" * 16


@pytest.fixture
def keyring(monkeypatch):
    """An in-memory omaseal plus a recorded exchange."""
    store: dict = {}
    seen: dict = {}
    monkeypatch.setattr(or_connect, "_stored", lambda: "key" in store)
    monkeypatch.setattr(or_connect.auth, "_omaseal_set", lambda s, a, v: store.__setitem__("key", (s, a, v)) or True)

    def exchange(code, verifier):
        seen.update(code=code, verifier=verifier)
        return KEY

    monkeypatch.setattr(or_connect, "_exchange", exchange)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    or_connect._pending.clear()
    return store, seen


def api(console, path, body=None):
    status, _, raw = console.request(
        "POST", path, body=json.dumps(body or {}),
        headers={"Content-Type": "application/json", "X-CSRF-Token": console.csrf()},
    )
    return status, json.loads(raw)


def begin(console):
    origin = f"http://127.0.0.1:{console.port}"
    status, out = api(console, "/api/openrouter/connect", {"origin": origin})
    assert status == 200
    q = parse_qs(urlsplit(out["url"]).query)
    return origin, out["url"], q


def test_connect_url_is_s256_pkce_and_calls_back_to_the_console(console, keyring):
    origin, url, q = begin(console)
    assert url.startswith("https://openrouter.ai/auth?")
    assert q["code_challenge_method"] == ["S256"]
    cb = urlsplit(q["callback_url"][0])
    assert f"{cb.scheme}://{cb.netloc}{cb.path}" == f"{origin}/settings" and "oc_state" in parse_qs(cb.query)


def test_full_flow_stores_key_in_omaseal_and_never_returns_it(console, keyring):
    store, seen = keyring
    _, url, q = begin(console)
    state = parse_qs(urlsplit(q["callback_url"][0]).query)["oc_state"][0]
    status, out = api(console, "/api/openrouter/complete", {"code": "code-123", "state": state})
    assert status == 200 and out == {"connected": True, "source": "omaseal"}
    assert store["key"] == ("openrouter", "open-computer", KEY)
    # the verifier sent to OpenRouter hashes to the challenge in the sign-in URL
    digest = base64.urlsafe_b64encode(hashlib.sha256(seen["verifier"].encode()).digest()).rstrip(b"=").decode()
    assert digest == q["code_challenge"][0] and seen["code"] == "code-123"
    assert KEY not in json.dumps(out) and seen["verifier"] not in url
    assert console.get("/api/openrouter")[2] == '{"connected": true, "source": "omaseal"}'


def test_state_is_one_shot_unknown_and_expiry_checked(console, keyring):
    store, _ = keyring
    _, _, q = begin(console)
    state = parse_qs(urlsplit(q["callback_url"][0]).query)["oc_state"][0]
    assert api(console, "/api/openrouter/complete", {"code": "c", "state": "forged"})[0] == 400
    assert api(console, "/api/openrouter/complete", {"code": "", "state": state})[0] == 400
    assert api(console, "/api/openrouter/complete", {"code": "c", "state": state})[0] == 200
    assert api(console, "/api/openrouter/complete", {"code": "c", "state": state})[0] == 400  # replay
    _, _, q = begin(console)
    state = parse_qs(urlsplit(q["callback_url"][0]).query)["oc_state"][0]
    verifier, cb, _ = or_connect._pending[state]
    or_connect._pending[state] = (verifier, cb, 0.0)
    assert api(console, "/api/openrouter/complete", {"code": "c", "state": state})[0] == 400


def test_foreign_origin_refused_and_mutations_need_csrf(console, keyring):
    assert api(console, "/api/openrouter/connect", {"origin": "http://evil.example"})[0] == 400
    for path in ("connect", "complete", "disconnect"):
        status, *_ = console.post(f"/api/openrouter/{path}", {}, csrf=False)
        assert status == 403


def test_exchange_failure_is_generic_and_saves_nothing(console, keyring, monkeypatch):
    store, _ = keyring

    def boom(code, verifier):
        raise RuntimeError(f"upstream said {KEY}")

    monkeypatch.setattr(or_connect, "_exchange", boom)
    _, _, q = begin(console)
    state = parse_qs(urlsplit(q["callback_url"][0]).query)["oc_state"][0]
    status, out = api(console, "/api/openrouter/complete", {"code": "c", "state": state})
    assert status == 400 and KEY not in json.dumps(out) and "key" not in store


def test_status_and_disconnect(console, keyring, monkeypatch):
    store, _ = keyring
    assert json.loads(console.get("/api/openrouter")[2]) == {"connected": False, "source": None}
    monkeypatch.setenv("OPENROUTER_API_KEY", "from-env")
    assert json.loads(console.get("/api/openrouter")[2]) == {"connected": True, "source": "env"}
    monkeypatch.delenv("OPENROUTER_API_KEY")
    store["key"] = ("openrouter", "open-computer", KEY)

    class P:
        returncode = 0

    def rm(cmd, **kw):
        assert cmd == ["omaseal", "rm", "openrouter", "open-computer"]
        store.clear()
        return P()

    monkeypatch.setattr(or_connect.subprocess, "run", rm)
    status, out = api(console, "/api/openrouter/disconnect")
    assert status == 200 and out == {"connected": False, "source": None}


def test_settings_serves_the_spa_shell(console):
    status, _, html = console.get("/settings")
    assert status == 200 and 'id="root"' in html
