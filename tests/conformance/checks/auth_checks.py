"""AUTH (passkey gate) checks — the gate itself is a matrix row.

Adapter: ops fixture → (base_url, auth_instance, http_get_no_redirect).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from harness import CheckFailed, check


def _status(get, path, **kw):
    try:
        r = get(path, **kw)
        return r.status, dict(r.headers), r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode()


@check("AUTH", "unauthed view request redirects to /login — nothing leaks")
def unauthed_redirects(ops) -> None:
    base, _auth, get = ops
    for path in ("/", "/run", "/route?q=x"):
        code, hdrs, _ = _status(get, path)
        if code != 302 or "/login" not in hdrs.get("Location", ""):
            raise CheckFailed(f"{path} → {code} (expected 302 → /login)")


@check("AUTH", "login page renders the ceremony, no webauthn-less escape")
def login_page_renders(ops) -> None:
    base, _auth, get = ops
    code, _h, body = _status(get, "/login")
    if code != 200 or "navigator.credentials" not in body:
        raise CheckFailed(f"/login → {code}, ceremony JS missing")


@check("AUTH", "register/begin requires a minted enroll token")
def register_requires_token(ops) -> None:
    _b, _a, get = ops
    code, _h, body = _status(get, "/webauthn/register/begin", method="POST", data=b"{}")
    if code != 403:
        raise CheckFailed(f"register/begin without token → {code}")


@check("AUTH", "enroll token is single-use and rp-bound")
def token_single_use(ops) -> None:
    _b, auth, get = ops
    tok = auth.mint_token("127.0.0.1")  # fixture requests hit 127.0.0.1:port
    body = json.dumps({"token": tok}).encode()
    c1, _h, b1 = _status(get, "/webauthn/register/begin", method="POST", data=body)
    if c1 != 200 or "publicKey" not in b1:
        raise CheckFailed(f"first use → {c1}: {b1[:120]}")
    c2, _h, _ = _status(get, "/webauthn/register/begin", method="POST", data=body)
    if c2 != 403:
        raise CheckFailed(f"second use → {c2} (replay accepted)")
    wrong_rp = auth.mint_token("other.example")
    c3, _h, _ = _status(
        get, "/webauthn/register/begin", method="POST",
        data=json.dumps({"token": wrong_rp}).encode(),
    )
    if c3 != 403:
        raise CheckFailed(f"wrong-rp token → {c3}")


@check("AUTH", "session cookie round-trips; tamper and wrong-rp both die")
def session_integrity(ops) -> None:
    _b, auth, get = ops
    auth.store.add(
        "127.0.0.1",
        {"id": "dGVzdA", "credential_data": "", "label": "test", "sign_count": 0, "created": 0},
    )
    cookie = auth._cookie("127.0.0.1", "luke", b"test")
    if auth.check_cookie("127.0.0.1", cookie) != "luke":
        raise CheckFailed("valid cookie rejected")
    if auth.check_cookie("other.example", cookie) is not None:
        raise CheckFailed("cookie accepted on wrong rp")
    tampered = cookie[:-1] + ("0" if cookie[-1] != "0" else "1")
    if auth.check_cookie("127.0.0.1", tampered) is not None:
        raise CheckFailed("tampered cookie accepted")
    code, _h, _ = _status(get, "/", headers={"Cookie": f"oc_sess={cookie}"})
    if code != 200:
        raise CheckFailed(f"authed / → {code}")
