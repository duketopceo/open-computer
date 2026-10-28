"""HTTP relay exposing fsproto mailbox ops to remote device agents (plan P8).

Endpoints (all under /relay/devices/<did>, bearer-authed to that device only):

    POST /heartbeat              -> {"ok": true, "device": {...}}  (also drains parked mail)
    GET  /inbox                  -> {"tasks": [tid, ...]}
    GET  /tasks/<tid>            -> {"body": {...task body...}}      (tid must be in did's inbox)
    POST /tasks/<tid>/claim      {"ttl": seconds} -> {"claimed": tid} | 409 leased | 410 final
    POST /tasks/<tid>/result     raw body -> {"ok": true} | 409 immutable | 410 final
    GET  /grants                 -> {"grants": {grant_id: {...}}}    (only this device's)
    POST /audit                  {"event": ..., ...} -> {"ok": true} (appended to orchestrator.ndjson)
"""

from __future__ import annotations

import hmac
import json
import ipaddress
import math
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from oc.fsproto import AlreadyLeased, FSAdapter, ResultImmutable, TaskFinal, adapter as fs_adapter
from oc.obs import bind, get_logger
from services.device import adapter as registry_adapter
from services.device.security import DeviceToken, valid_device_id
from services.gate.audit import AuditLog

log = get_logger("device_relay")

DID = r"(?P<did>[A-Za-z0-9._-]{1,128})"
TID = r"(?P<tid>[0-9a-f]{8,64})"
ROUTES = {
    ("POST", re.compile(rf"^/relay/devices/{DID}/heartbeat$")): "heartbeat",
    ("GET", re.compile(rf"^/relay/devices/{DID}/inbox$")): "inbox",
    ("GET", re.compile(rf"^/relay/devices/{DID}/tasks/{TID}$")): "task",
    ("POST", re.compile(rf"^/relay/devices/{DID}/tasks/{TID}/claim$")): "claim",
    ("POST", re.compile(rf"^/relay/devices/{DID}/tasks/{TID}/result$")): "result",
    ("GET", re.compile(rf"^/relay/devices/{DID}/grants$")): "grants",
    ("POST", re.compile(rf"^/relay/devices/{DID}/audit$")): "audit",
}
MAX_BODY = 1 << 20  # 1 MiB — results/audits stay small; nothing larger may arrive

# Event families a remote device may legitimately emit (exec/privacy/intake) —
# control-plane events (device_paired, grant_revoked, ...) can't be fabricated here.
DEVICE_EVENTS = ("capability_", "device_goal_", "device_privacy", "grant_check", "wisp_", "consent_", "envelope_", "parked_")


def _load_token(root: Path, did: str) -> DeviceToken | None:
    p = root / "devices" / f"{did}.token.json"
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    # field types are file-derived — validate before DeviceToken sees them
    if not (
        isinstance(data.get("device_id"), str)
        and isinstance(data.get("secret"), str)
        and isinstance(data.get("issued_at"), (int, float))
        and isinstance(data.get("expires_at"), (int, float))
        and isinstance(data.get("revoked", False), bool)
    ):
        return None
    try:
        return DeviceToken(**data)
    except TypeError:
        return None


class Handler(BaseHTTPRequestHandler):
    root: Path
    fs: FSAdapter
    audit: AuditLog

    def setup(self):
        super().setup()
        self.connection.settimeout(30)  # bound idle/dribbling connections before auth runs

    def log_message(self, *a):
        pass

    # --- plumbing ------------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype="application/json", headers: dict | None = None):
        self._status = code
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict):
        self._send(code, json.dumps(obj).encode())

    def _device(self, did: str) -> str | None:
        """Return the device_id iff the bearer token is this device's active secret."""
        auth = self.headers.get("Authorization", "")
        presented = auth[7:].strip() if auth.startswith("Bearer ") else ""
        tok = _load_token(self.root, did)
        if (
            not presented
            or not presented.isascii()
            or tok is None
            or tok.device_id != did
            or not tok.secret
            or not tok.active(time.time())
        ):
            return None
        return did if hmac.compare_digest(tok.secret.encode(), presented.encode()) else None

    def _mailbox(self, did: str) -> str:
        return f"device/{did}"

    def _in_inbox(self, did: str, tid: str) -> bool:
        return tid in self.fs.inbox_ids(self._mailbox(did))

    def _body(self) -> bytes | None:
        """Request body, or None when the declared length is absent, bad, or over MAX_BODY."""
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return None
        if n < 0 or n > MAX_BODY:
            return None
        data = self.rfile.read(n) if n else b""
        return data if len(data) == n else None  # short read = truncated upload

    # --- dispatch ------------------------------------------------------------
    def _route(self, method: str):
        path = urlparse(self.path).path
        for (m, rx), op in ROUTES.items():
            if m != method:
                continue
            match = rx.match(path)
            if match:
                return op, match.groupdict()
        return None, {}

    def _handle(self, method: str):
        t0 = time.monotonic()
        self._status = 0
        op, args = self._route(method)
        did = args.get("did")
        try:
            self._dispatch(method, op, args)
        finally:
            # Access log: route *name* (never the raw path or any header), status, latency, device id.
            ms = round((time.monotonic() - t0) * 1000, 1)
            quiet = self._status < 400 and op in ("heartbeat", "inbox")
            (log.debug if quiet else log.warning if self._status >= 400 else log.info)(
                "relay request", method=method, op=op or "unrouted", device_id=did, status=self._status, ms=ms)

    def _dispatch(self, method: str, op: str | None, args: dict):
        if op is None:
            return self._send(404, b"not found", "text/plain")
        did = args["did"]
        if not valid_device_id(did) or self._device(did) is None:
            try:
                self.audit.append({"event": "relay_auth_rejected", "device_id": did, "via": "relay"})
            except OSError as e:
                log.warning("relay auth audit write failed", error=type(e).__name__)
            return self._send(401, b"unauthorized", "text/plain", {"WWW-Authenticate": "Bearer"})
        handler = getattr(self, f"_{method.lower()}_{op}")
        with bind(service="device_relay"):
            handler(did, args)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    # --- ops -----------------------------------------------------------------
    def _post_heartbeat(self, did: str, _args):
        # fs+audit bound so a remote heartbeat drains the parked mailbox, same as local.
        reg = registry_adapter(self.root, fs=self.fs, audit=self.audit)
        try:
            dev = reg.heartbeat(did)
        except KeyError:
            return self._json(404, {"error": "unregistered device"})
        row = {
            "device_id": dev.device_id,
            "capabilities": list(dev.capabilities),
            "roots": list(dev.roots),
            "online": dev.online,
            "awake": dev.awake,
        }
        self._json(200, {"ok": True, "device": row})

    def _get_inbox(self, did: str, _args):
        self._json(200, {"tasks": self.fs.inbox_ids(self._mailbox(did))})

    def _get_task(self, did: str, args):
        tid = args["tid"]
        if not self._in_inbox(did, tid):
            return self._send(403, b"task not in your inbox", "text/plain")
        try:
            body = json.loads(self.fs.task_body(tid))
        except (OSError, ValueError):
            return self._send(404, b"task record unreadable", "text/plain")
        self._json(200, {"body": body})

    def _post_claim(self, did: str, args):
        tid = args["tid"]
        if not self._in_inbox(did, tid):
            return self._send(403, b"task not in your inbox", "text/plain")
        try:
            body = json.loads(self._body() or b"{}")
        except ValueError:
            body = None
        if body is not None and not isinstance(body, dict):
            return self._send(400, b"claim body must be a json object", "text/plain")
        ttl = (body or {}).get("ttl")
        if not isinstance(ttl, (int, float)) or not math.isfinite(ttl) or ttl <= 0:
            ttl = None
        try:
            self.fs.claim(self._mailbox(did), tid, ttl=ttl)
        except AlreadyLeased:
            return self._send(409, b"already leased", "text/plain")
        except TaskFinal:
            return self._send(410, b"task final", "text/plain")
        except ValueError as e:
            return self._send(400, str(e).encode(), "text/plain")
        except OSError as e:
            return self._send(404, f"task gone: {e}".encode(), "text/plain")
        self._json(200, {"claimed": tid})

    def _post_result(self, did: str, args):
        tid = args["tid"]
        if not self._in_inbox(did, tid):
            return self._send(403, b"task not in your inbox", "text/plain")
        body = self._body()
        if body is None:
            return self._send(413, b"body missing, truncated or over 1 MiB", "text/plain")
        if not body:
            return self._send(400, b"empty result refused - a tombstoned empty result is permanent", "text/plain")
        try:
            self.fs.outbox_write(self._mailbox(did), tid, body)
        except ResultImmutable:
            return self._send(409, b"result immutable", "text/plain")
        except TaskFinal:
            return self._send(410, b"task final", "text/plain")
        except OSError as e:
            return self._send(404, f"task gone: {e}".encode(), "text/plain")
        self._json(200, {"ok": True})

    def _get_grants(self, did: str, _args):
        p = self.root / "devices" / "grants.json"
        try:
            all_grants = json.loads(p.read_text())
        except (OSError, ValueError):
            all_grants = {}
        own = {gid: g for gid, g in all_grants.items() if isinstance(g, dict) and g.get("device_id") == did}
        self._json(200, {"grants": own})

    def _post_audit(self, did: str, _args):
        try:
            event = json.loads(self._body() or b"{}")
        except ValueError:
            return self._send(400, b"bad json", "text/plain")
        if not isinstance(event, dict) or not isinstance(event.get("event"), str):
            return self._send(400, b"audit event must be an object with a string event field", "text/plain")
        if not event["event"].startswith(DEVICE_EVENTS):
            return self._send(403, b"event family not device-emittable", "text/plain")
        self.audit.append({**event, "device_id": did, "via": "relay"})
        self._json(200, {"ok": True})


def make_server(port: int, root: Path | str, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    root = Path(root)

    class Bound(Handler):
        pass

    Bound.root = root
    Bound.fs = fs_adapter(root / "run")
    Bound.audit = AuditLog(root / "orchestrator.ndjson")
    srv = ThreadingHTTPServer((host, port), Bound)
    srv.daemon_threads = True
    return srv


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="open-computer remote device relay")
    ap.add_argument("--root", required=True, help="orchestrator root (same as oc-device --root)")
    ap.add_argument("--port", type=int, default=8792)
    ap.add_argument("--bind", default="127.0.0.1", help="loopback or a tailscale IP — never public")
    a = ap.parse_args(argv)
    try:
        wildcard = ipaddress.ip_address(a.bind).is_unspecified
    except ValueError:
        wildcard = not a.bind  # empty binds everything; a hostname is an explicit choice
    if wildcard:
        ap.error("refusing to bind every interface — use a specific loopback or tailscale IP")
    srv = make_server(a.port, a.root, host=a.bind)
    log.info("device relay listening", bind=a.bind, port=a.port)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
