"""Passkey/WebAuthn gate for the workplace surface.

One credential store per relying party (origin scope). Sessions are
hmac-signed cookies bound to rp + credential. Enrollment is token-gated:
`mint` produces a single-use token file (CLI-side), `/enroll?token=`
consumes it via atomic rename — no open registration window.

Deps: fido2 (python-fido2). Everything else stdlib.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections.abc import Mapping
from enum import Enum
from pathlib import Path
from typing import Any

from fido2.server import Fido2Server
from fido2.utils import websafe_decode, websafe_encode
from fido2.webauthn import (
    AttestedCredentialData,
    AuthenticationResponse,
    PublicKeyCredentialRpEntity,
    PublicKeyCredentialUserEntity,
    RegistrationResponse,
)

DATA_DIR = Path.home() / ".local" / "share" / "oc"
CREDS_PATH = DATA_DIR / "credentials.json"
KEY_PATH = DATA_DIR / "session-key"
ENROLL_DIR = DATA_DIR / "enroll"
SESSION_TTL = 12 * 3600
STATE_TTL = 300
TOKEN_TTL = 600


def _b64e(b: bytes) -> str:
    return websafe_encode(b)


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, (bytes, bytearray)):
        return _b64e(bytes(obj))
    if isinstance(obj, Mapping):
        return {k: _jsonable(obj[k]) for k in obj}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    if isinstance(obj, Enum):
        return obj.value
    raise TypeError(f"unserializable: {type(obj)}")


def rp_for(host: str) -> str:
    """Relying-party id for a request host.

    shippedit.dev and all subdomains share the parent rp so one passkey
    covers oc./docketry./etc. Everything else (tailnet names, localhost)
    scopes to the literal host.
    """
    h = host.split(":", 1)[0].lower()
    if h == "shippedit.dev" or h.endswith(".shippedit.dev"):
        return "shippedit.dev"
    return h


def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


class CredentialStore:
    def __init__(self, path: Path = CREDS_PATH):
        self.path = path
        self._creds: dict[str, list[dict]] = json.loads(path.read_text()) if path.exists() else {}

    def save(self) -> None:
        _write_private(self.path, json.dumps(self._creds, indent=2).encode())

    def for_rp(self, rp: str) -> list[dict]:
        return self._creds.get(rp, [])

    def add(self, rp: str, entry: dict) -> None:
        self._creds.setdefault(rp, []).append(entry)
        self.save()

    def update(self, rp: str, cred_id: bytes, **fields) -> None:
        for c in self._creds.get(rp, []):
            if websafe_decode(c["id"]) == cred_id:
                c.update(fields)
                self.save()
                return

    def by_id(self, rp: str, cred_id: bytes) -> dict | None:
        for c in self.for_rp(rp):
            if websafe_decode(c["id"]) == cred_id:
                return c
        return None

    def attested(self, rp: str) -> list[AttestedCredentialData]:
        out = []
        for c in self.for_rp(rp):
            try:
                out.append(AttestedCredentialData(websafe_decode(c["credential_data"])))
            except Exception:
                continue
        return out


class Auth:
    """WebAuthn ceremonies + session cookies + enroll tokens."""

    def __init__(self, store: CredentialStore | None = None, key_path: Path = KEY_PATH):
        self.store = store or CredentialStore()
        key_path.parent.mkdir(parents=True, exist_ok=True)
        if not key_path.exists():
            fd = os.open(key_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                os.write(fd, secrets.token_bytes(32))
            finally:
                os.close(fd)
        key = key_path.read_bytes()
        if len(key) != 32:
            raise RuntimeError(f"session key corrupt: {key_path}")
        self._key = key
        self._pending: dict[str, tuple[Any, str, str, float]] = {}
        self._lock = threading.Lock()

    # -- ceremonies --------------------------------------------------

    def _server(self, rp: str) -> Fido2Server:
        return Fido2Server(PublicKeyCredentialRpEntity(id=rp, name="open-computer"))

    def _stash(self, state: Any, rp: str, kind: str) -> str:
        tok = _b64e(secrets.token_bytes(24))
        with self._lock:
            now = time.time()
            for k in [k for k, v in self._pending.items() if v[3] <= now]:
                del self._pending[k]
            self._pending[tok] = (state, rp, kind, now + STATE_TTL)
        return tok

    def _pop(self, tok: str, kind: str) -> tuple[Any, str] | None:
        with self._lock:
            item = self._pending.pop(tok, None)
        if not item or item[3] < time.time() or item[2] != kind:
            return None
        return item[0], item[1]

    def begin_register(self, rp: str, user_name: str) -> tuple[dict, str]:
        user = PublicKeyCredentialUserEntity(
            id=secrets.token_bytes(16), name=user_name, display_name=user_name
        )
        options, state = self._server(rp).register_begin(
            user,
            credentials=[
                {"type": "public-key", "id": websafe_decode(c["id"])}
                for c in self.store.for_rp(rp)
            ],
        )
        return _jsonable(dict(options)), self._stash(state, rp, "reg")

    def complete_register(self, tok: str, response_json: str, label: str = "") -> str | None:
        got = self._pop(tok, "reg")
        if not got:
            return None
        state, rp = got
        auth_data = self._server(rp).register_complete(
            state, RegistrationResponse.from_dict(json.loads(response_json))
        )
        cred = auth_data.credential_data
        if cred is None:
            return None
        cred_id = _b64e(cred.credential_id)
        self.store.add(
            rp,
            {
                "id": cred_id,
                "credential_data": _b64e(bytes(cred)),
                "label": label or f"passkey-{len(self.store.for_rp(rp)) + 1}",
                "sign_count": auth_data.counter,
                "created": time.time(),
            },
        )
        return cred_id

    def begin_auth(self, rp: str) -> tuple[dict, str]:
        options, state = self._server(rp).authenticate_begin(
            credentials=[
                {"type": "public-key", "id": websafe_decode(c["id"])}
                for c in self.store.for_rp(rp)
            ],
        )
        return _jsonable(dict(options)), self._stash(state, rp, "auth")

    def complete_auth(self, tok: str, response_json: str) -> tuple[str, str] | None:
        got = self._pop(tok, "auth")
        if not got:
            return None
        state, rp = got
        resp = AuthenticationResponse.from_dict(json.loads(response_json))
        matched = self._server(rp).authenticate_complete(
            state, self.store.attested(rp), resp
        )
        entry = self.store.by_id(rp, resp.raw_id)
        label = entry["label"] if entry else "passkey"
        counter = resp.response.authenticator_data.counter
        if entry and counter:
            if counter <= entry.get("sign_count", 0):
                return None
            self.store.update(rp, resp.raw_id, sign_count=counter)
        return label, self._cookie(rp, label, matched.credential_id)

    # -- sessions ----------------------------------------------------

    def _cookie(self, rp: str, identity: str, cred_id: bytes) -> str:
        exp = int(time.time()) + SESSION_TTL
        body = ".".join(
            [
                base64.urlsafe_b64encode(rp.encode()).decode(),
                base64.urlsafe_b64encode(identity.encode()).decode(),
                base64.urlsafe_b64encode(cred_id).decode(),
                str(exp),
            ]
        )
        sig = hmac.new(self._key, body.encode(), hashlib.sha256).hexdigest()
        return f"{body}.{sig}"

    def check_cookie(self, rp: str, raw: str | None) -> str | None:
        if not raw:
            return None
        try:
            rp_b64, id_b64, cred_b64, exp_s, sig = raw.rsplit(".", 4)
            body = f"{rp_b64}.{id_b64}.{cred_b64}.{exp_s}"
            if not hmac.compare_digest(
                hmac.new(self._key, body.encode(), hashlib.sha256).hexdigest(), sig
            ):
                return None
            if int(exp_s) < time.time():
                return None
            if base64.urlsafe_b64decode(rp_b64.encode()).decode() != rp:
                return None
            if not self.store.by_id(rp, base64.urlsafe_b64decode(cred_b64.encode())):
                return None  # credential deleted → session dead
            return base64.urlsafe_b64decode(id_b64.encode()).decode()
        except Exception:
            return None

    # -- enroll tokens (one file per token; consume = atomic rename) --

    def mint_token(self, rp: str) -> str:
        tok = _b64e(secrets.token_bytes(24))
        rec = {"rp": rp, "exp": time.time() + TOKEN_TTL}
        _write_private(ENROLL_DIR / tok, json.dumps(rec).encode())
        return tok

    def consume_token(self, rp: str, tok: str) -> bool:
        src = ENROLL_DIR / tok
        if not src.exists() or "/" in tok or ".." in tok:
            return False
        try:
            claimed = src.with_name(src.name + ".used")
            src.rename(claimed)  # atomic claim — second consumer loses
        except OSError:
            return False
        try:
            rec = json.loads(claimed.read_text())
        except Exception:
            return False
        return bool(rec.get("rp") == rp and rec.get("exp", 0) > time.time())


if __name__ == "__main__":
    import sys

    if len(sys.argv) >= 3 and sys.argv[1] == "mint":
        print(Auth().mint_token(sys.argv[2]))
    else:
        print("usage: python -m services.opsview.auth mint <rp-id>", file=sys.stderr)
        sys.exit(2)
