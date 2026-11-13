"""Check every catalog model for a reachable OpenRouter endpoint in this key's data region.

    OC_OPENROUTER_BASE_URL=https://us.openrouter.ai/api/v1 uv run python scripts/check_catalog_region.py

Uses OPENROUTER_API_KEY or OC_OPENROUTER_API_KEY. Prints flagged ids (one per line) and exits 1 if
any catalog model has no endpoint. Never prints the key. Attribution headers are sent: the US host
rejects requests without them. A 403 that names a permitted regional hostname is retried there.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT)]

from services.openrouter import attribution  # noqa: E402
from services.openrouter.client import base_url  # noqa: E402
from services.router import model_catalog as mc  # noqa: E402
from services.router.reachability import flags  # noqa: E402

_REGION_HOST = re.compile(
    r"permits requests only on these OpenRouter hostnames:\s*([a-z0-9.-]+)",
    re.IGNORECASE,
)


def _key() -> str:
    return os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OC_OPENROUTER_API_KEY") or ""


def _redact(s: str) -> str:
    key = _key()
    if key:
        s = s.replace(key, "[KEY]")
    return re.sub(r"sk-[a-zA-Z0-9_-]{8,}", "[KEYLIKE]", s)


def _headers() -> dict[str, str]:
    key = _key()
    return {
        "Authorization": f"Bearer {key}" if key else "",
        "Content-Type": "application/json",
        "User-Agent": "open-computer/0.1 (+https://github.com/duketopceo/open-computer)",
        **attribution.headers(),
    }


def _get(url: str, timeout: float = 30.0) -> tuple[int, object]:
    req = urllib.request.Request(url, headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            body: object = json.loads(raw)
        except json.JSONDecodeError:
            body = _redact(raw[:300])
        return e.code, body
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        raise SystemExit(f"request failed: {_redact(f'{type(e).__name__}: {e}')}") from None


def _error_message(body: object) -> str:
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        return str(body["error"].get("message") or "")
    return _redact(str(body)[:400])


def _regional_base(current: str, body: object) -> str | None:
    """The regional API base a 403 told us to use, or None if this is not that error."""
    m = _REGION_HOST.search(_error_message(body))
    if not m:
        return None
    host = m.group(1).rstrip(".")
    if host in current:
        return None
    return f"https://{host}/api/v1"


def main() -> int:
    if not _key():
        print("set OPENROUTER_API_KEY (or OC_OPENROUTER_API_KEY) to run this check", file=sys.stderr)
        return 2
    base = base_url()
    status, body = _get(f"{base}/models")
    regional = _regional_base(base, body) if status == 403 else None
    if regional:
        print(f"retrying on regional host {regional}", file=sys.stderr)
        base = regional
        status, body = _get(f"{base}/models")
    if status != 200 or not isinstance(body, dict) or not isinstance(body.get("data"), list):
        print(f"GET {base}/models -> HTTP {status}: {_error_message(body)}", file=sys.stderr)
        return 2
    model_ids = {m["id"] for m in body["data"] if isinstance(m, dict) and isinstance(m.get("id"), str)}
    catalog_ids = [e.id for e in mc.CATALOG.entries]
    endpoints: dict[str, object] = {}
    for mid in catalog_ids:
        st, payload = _get(f"{base}/models/{mid}/endpoints")
        endpoints[mid] = st if st >= 400 else payload
    found = flags(catalog_ids=catalog_ids, model_ids=model_ids, endpoints=endpoints)
    if not found:
        print(f"ok: {len(catalog_ids)} catalog models have a reachable endpoint")
        return 0
    for f in found:
        print(f"{f.id}: {f.reason}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
