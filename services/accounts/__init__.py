"""Accounts: one record per signed-in identity.

Sign-in itself is the console's existing gate (Cloudflare Access email, a passkey, or loopback `local`).
An account gives that identity a stable id (used as the Composio user id, never the email), a display name,
and a creation time. Stored as one JSON file, written atomically.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path

from oc.obs import get_logger

log = get_logger("accounts")

NAME_MAX = 80
_lock = threading.Lock()


def accounts_path() -> Path:
    return Path(os.environ.get("OC_ACCOUNTS_PATH", str(Path.home() / ".local/share/oc/accounts.json")))


def account_id(identity: str) -> str:
    """Stable id for an identity: `oc_` + 16 hex of sha256 of the lower-cased identity."""
    return "oc_" + hashlib.sha256(identity.strip().lower().encode()).hexdigest()[:16]


def _load(p: Path) -> dict[str, dict]:
    try:
        data = json.loads(p.read_text())
    except FileNotFoundError:
        return {}
    except ValueError:
        log.error("accounts file unreadable; starting empty in memory", path=str(p))
        return {}
    return data if isinstance(data, dict) else {}


def _save(p: Path, data: dict[str, dict]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    os.replace(tmp, p)


def _public(acct: dict, identity: str) -> dict:
    via = "local" if identity == "local" else "email" if "@" in identity else "passkey"
    return {
        "id": acct["id"],
        "email": identity if "@" in identity else "",
        "name": acct.get("name", ""),
        "created": acct["created"],
        "via": via,
    }


def get_or_create(identity: str) -> dict:
    """The account for `identity`, created on first sight."""
    aid = account_id(identity)
    p = accounts_path()
    with _lock:
        data = _load(p)
        acct = data.get(aid)
        if acct is None:
            acct = {"id": aid, "name": "", "created": time.time()}
            data[aid] = acct
            _save(p, data)
            log.info("account created", account=aid)
    return _public(acct, identity)


def set_name(identity: str, name: object) -> dict:
    """Change the display name. Raises ValueError for anything but a short string."""
    if not isinstance(name, str) or len(name.strip()) > NAME_MAX:
        raise ValueError(f"Name must be text, at most {NAME_MAX} characters.")
    get_or_create(identity)
    aid = account_id(identity)
    p = accounts_path()
    with _lock:
        data = _load(p)
        data[aid]["name"] = name.strip()
        _save(p, data)
        acct = data[aid]
    return _public(acct, identity)
