"""open-computer operator console (Silver Lining).

stdlib only, no build step. Every number is live (probed on request); nothing
is stubbed. Sections: Overview, Sandboxes, Runs, Approvals, Router, Audit.
Gate: passkey (auth.py).

Safety rules (docs/design/silver-lining.md section 14):
  * No GET has side effects. GET handlers only read; anything that boots a
    microVM, spends money, decides an approval or writes a log is POST.
  * Every POST needs the session, a CSRF token bound to that session, and a
    same-origin Origin header. Spending or leaving-the-box actions also need
    an explicit confirm step that names the destination and cost.

Run:  CUBE_API_URL=... CUBE_API_KEY=... python -m services.opsview.server
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from oc.obs import bind, get_logger
from services.gate.gate import Gate
from services.gate.queue import ApprovalQueue, QueueError
from services.agents.approvals import ApprovalError
from services.opsview.agentgrants import GrantError
from services.opsview import agentjobs, automations, capabilities, consoleapi, devices, goalruns
from services.opsview import runs as runstore
from services.opsview import views
from services.opsview.auth import Auth, SESSION_TTL, rp_for
from services.opsview.webauthn_page import enroll_page, login_page
from services.orchestrator import engines as goal_engines
from services.workspace import routes as workspace

log = get_logger("opsview")

REPO = Path(__file__).resolve().parents[2]
STATIC = Path(__file__).resolve().parent / "static"
UI_DIST = REPO / "ui" / "dist"
_ASSET_TYPES = {
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".png": "image/png",
    ".ico": "image/x-icon",
}
STATIC_TYPES = {
    "silver-lining.css": "text/css",
    "console.css": "text/css",
    "console.js": "text/javascript",
    "theme-init.js": "text/javascript",
    "work.js": "text/javascript",
    "favicon.svg": "image/svg+xml",
}
CSP = (
    "default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
)


def _csrf_secret() -> bytes:
    """Stable across restarts: CSRF tokens are bound to the session cookie,
    so a fresh per-process secret would invalidate every open form on restart."""
    p = Path(os.environ.get("OC_CSRF_SECRET", str(Path.home() / ".local/share/oc/csrf-secret")))
    try:
        st = p.stat()
        if st.st_mode & 0o077:
            # Readable by others = treat as leaked: rotate, don't trust it.
            log.warning("csrf secret has loose permissions; rotating")
            p.unlink()
        else:
            raw = p.read_bytes()
            if raw:
                return raw
    except OSError:
        pass
    raw = secrets.token_bytes(32)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.{os.getpid()}")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)  # 0600 from birth
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
        os.replace(tmp, p)
    except OSError as e:
        log.warning("csrf secret not persisted; tokens reset on restart", error=type(e).__name__)
    return raw


CSRF_SECRET = _csrf_secret()
ACCESS_IDENTITY_URL = "https://duketopceo.cloudflareaccess.com/cdn-cgi/access/get-identity"


def access_identity(cf_token: str) -> str | None:
    """Ask Cloudflare who a CF_Authorization token belongs to. Server-verified
    (no local crypto to get wrong); only called when no app session exists —
    once per login. Any failure returns None → the passkey gate stays."""
    if not cf_token:
        return None
    url = os.environ.get("OC_ACCESS_IDENTITY_URL", ACCESS_IDENTITY_URL)
    try:
        req = urllib.request.Request(url, headers={"Cookie": f"CF_Authorization={cf_token}"})
        with urllib.request.urlopen(req, timeout=4) as r:
            d = json.loads(r.read().decode())
        email = str(d.get("email") or "").strip().lower()
        return email or None
    except Exception as e:
        log.warning("access identity lookup failed", error=type(e).__name__)
        return None


# ------------------------------------------------------------------ config


def ledger_path() -> Path:
    return Path(os.environ.get("OC_ROUTER_LEDGER", "/tmp/oc-audit/router-ledger.jsonl"))


def audit_path() -> Path:
    return Path(os.environ.get("OC_AUDIT", "/tmp/oc-audit/audit.ndjson"))


def approvals_path() -> Path:
    return Path(os.environ.get("OC_APPROVALS", "/tmp/oc-audit/approvals.ndjson"))


def oc_root() -> Path:
    """Orchestrator root — `devices/` and `run/` live under it (deploy/oc-device.service)."""
    return Path(os.environ.get("OC_ROOT", str(Path.home() / ".local/share/oc")))


def daily_cap() -> float:
    return float(os.environ.get("OC_DAILY_CAP", "1.0"))


def router_base() -> str:
    return os.environ.get("OPENROUTER_BASE", "https://openrouter.ai/api")


def paid_router() -> bool:
    return bool(os.environ.get("OPENROUTER_API_KEY"))


# ------------------------------------------------------------- read helpers
# Everything below GET-reachable must not create, write or spend. Tests patch
# the mutation paths and fail if a GET touches one.


def _get(url: str, key: str, timeout: float = 3.0) -> tuple[int, str]:
    req = urllib.request.Request(url)
    if key:
        req.add_header("X-API-Key", key)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode()
    except Exception as e:
        return 0, str(e)


def fleet_state() -> dict:
    api = os.environ.get("CUBE_API_URL", "").rstrip("/")
    key = os.environ.get("CUBE_API_KEY", "")
    st = {"configured": bool(api), "up": False, "sandboxes": [], "api_host": views.host_of(api) if api else ""}
    if not api:
        return st
    code, _ = _get(f"{api}/health", key)
    if code != 200:
        return st
    st["up"] = True
    _, body = _get(f"{api}/sandboxes", key)
    try:
        data = json.loads(body)
        st["sandboxes"] = [s for s in data if isinstance(s, dict)] if isinstance(data, list) else []
    except ValueError:
        pass
    return st


def read_ndjson(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def day_spend() -> float:
    today = time.strftime("%Y-%m-%d")
    return sum(
        float(r.get("cost", 0) or 0)
        for r in read_ndjson(ledger_path())
        if time.strftime("%Y-%m-%d", time.localtime(r.get("ts", 0))) == today
    )


# --------------------------------------------------------- mutation paths


def _run_matrix() -> str:
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    try:
        p = subprocess.run(
            [
                "uv",
                "run",
                "--with",
                "pytest",
                "--with",
                "fido2",
                "--with-editable",
                str(Path.home() / "src/cubesandbox/sdk/python"),
                "pytest",
                "tests/conformance",
                "-q",
            ],
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        return p.stdout + p.stderr
    except Exception as e:
        return f"matrix run failed: {e}"


def _route(q: str) -> dict:
    sys.path.insert(0, str(REPO))
    from services.router import default_router

    r = default_router(str(ledger_path()), daily_cap=daily_cap())
    d = r.route(q)
    return {
        "route": d.route,
        "tier": d.tier,
        "engine": d.engine,
        "confidence": d.confidence,
        "cost": d.cost,
        "ms": round(d.ms * 1000) if d.ms < 10 else round(d.ms),
    }


def _submit_goal(goal: str, who: str) -> str:
    """Spending path behind POST /goals confirm. Tests patch this."""
    return goalruns.submit(goal, goal_engines.decompose_engine(), goal_engines.work_fn(), by=who)


def _resume_goals() -> None:
    """Re-drive goal runs orphaned by a restart. Fails soft — disk is truth."""
    try:
        n = goalruns.resume(goal_engines.work_fn())
        log.info("goal runs resumed", count=n) if n else log.debug("no goal runs to resume")
    except Exception as e:
        log.error("goal run resume failed", error=type(e).__name__)


def _resume_agent_jobs() -> None:
    """Re-drive agent jobs left unfinished by a restart (needs a configured runner). Fails soft — the trail is truth."""
    try:
        n = agentjobs.resume_all()
        log.info("agent jobs resumed", count=n) if n else log.debug("no agent jobs to resume")
    except Exception as e:
        log.error("agent job resume failed", error=type(e).__name__)


# ----------------------------------------------------------------- routes
# (pattern, method name). The tests enumerate these tables to prove GETs are
# side-effect free and that every POST is CSRF-protected.

GET_ROUTES = [
    (r"/", "g_spa"),
    (r"/assets/(?P<file>[^/]+)", "g_asset"),
    (r"/api/session", "g_api_session"),
    (r"/api/runs", "g_api_runs"),
    (r"/api/runs/(?P<id>goal_[0-9a-f]{8})", "g_api_run"),
    (r"/api/agent-jobs", "g_api_agent_jobs"),
    (r"/api/agent-jobs/(?P<id>[0-9a-f]{10})", "g_api_agent_job"),
    (r"/api/approvals", "g_api_approvals"),
    (r"/api/overview", "g_api_overview"),
    (r"/work", "g_work"),
    (r"/overview", "g_spa_route"),
    (r"/sandboxes", "g_spa_route"),
    (r"/sandboxes/(?P<id>[^/]+)", "g_spa_route"),
    (r"/devices", "g_spa_route"),
    (r"/goals", "g_spa_route"),
    (r"/goals/new", "g_goal_new"),
    (r"/goals/(?P<id>goal_[0-9a-f]{8})", "g_goal"),
    (r"/skills", "g_skills"),
    (r"/automations", "g_automations"),
    (r"/runs", "g_spa_route"),
    (r"/runs/new", "g_run_new"),
    (r"/runs/(?P<id>run_[0-9a-f]{8})", "g_run"),
    (r"/approvals", "g_spa_route"),
    (r"/router", "g_spa_route"),
    (r"/audit", "g_spa_route"),
    (r"/api/devices", "g_api_devices"),
    (r"/api/sandboxes", "g_api_sandboxes"),
    (r"/api/router", "g_api_router"),
    (r"/api/audit", "g_api_audit"),
    (r"/_frag/status", "g_frag_status"),
    (r"/_frag/work", "g_frag_work"),
]
REDIRECTS = {"/fleet": "/sandboxes", "/matrix": "/runs", "/gate": "/approvals", "/ops": "/"}
POST_ROUTES = [
    (r"/api/agent-jobs", "p_agent_submit"),
    (r"/api/agent-jobs/(?P<id>[0-9a-f]{10})/approvals/(?P<aid>ag_[0-9a-f]{8})/approve", "p_agent_approve"),
    (r"/api/agent-jobs/(?P<id>[0-9a-f]{10})/approvals/(?P<aid>ag_[0-9a-f]{8})/deny", "p_agent_deny"),
    (r"/api/transcribe", "p_transcribe"),
    (r"/runs", "p_runs"),
    (r"/goals", "p_goals"),
    (r"/api/router/test", "p_api_router_test"),
    (r"/api/approvals/(?P<id>ap_[0-9a-f]{8})/approve", "p_api_approve"),
    (r"/api/approvals/(?P<id>ap_[0-9a-f]{8})/send-back", "p_api_sendback"),
    (r"/approvals/(?P<id>ap_[0-9a-f]{8})/approve", "p_approve"),
    (r"/approvals/(?P<id>ap_[0-9a-f]{8})/send-back", "p_sendback"),
    (r"/automations", "p_automations"),
    (r"/automations/(?P<id>auto-[0-9a-f]{8})/pause", "p_auto_pause"),
    (r"/automations/(?P<id>auto-[0-9a-f]{8})/trigger", "p_auto_trigger"),
    (r"/automations/(?P<id>auto-[0-9a-f]{8})/delete", "p_auto_delete"),
]
# Old side-effecting GET URLs now answer 405 instead of acting.
POST_ONLY_LEGACY = {"/run", "/route"}


def match(table, path):
    for pat, name in table:
        m = re.fullmatch(pat, path)
        if m:
            return name, m.groupdict()
    return None, {}


_RID = re.compile(r"[A-Za-z0-9._-]{8,64}")


def _actor_label(who: str | None) -> str | None:
    """A stable pseudonym for logs: 'local' stays readable, anyone else is a short hash (never an email)."""
    if not who:
        return None
    return who if who == "local" else "u_" + hashlib.sha256(who.encode()).hexdigest()[:8]


def route_label(method: str, path: str) -> str:
    """The route *template* (handler name), never the raw path: ids stay out of the access log's cardinality."""
    table = GET_ROUTES if method == "GET" else POST_ROUTES
    name, _ = match(table, path)
    if name == "g_spa_route":
        return "spa/" + path.split("/")[1]  # one label per SPA page; ids never reach the log
    if name:
        return name
    if path.startswith("/static/") or path.startswith("/assets/"):
        return "static"
    if path.startswith("/webauthn/"):
        return "webauthn"
    if path in REDIRECTS or path in ("/login", "/enroll", "/"):
        return {"/": "spa"}.get(path, path.lstrip("/") or "spa")
    if workspace.is_ws_path(path):
        return "workspace"
    return "other"


class Handler(BaseHTTPRequestHandler):
    auth = Auth()
    _status = 0
    _reason: str | None = None
    _actor: str | None = None
    _request_id: str | None = None

    def send_response(self, code, message=None):
        self._status = code
        super().send_response(code, message)
        if self._request_id:
            self.send_header("X-Request-Id", self._request_id)

    def _observed(self, method: str, fn) -> None:
        """Request id + access log around one request. The id comes from a well-formed inbound X-Request-Id
        (so a proxy's id carries through) or is generated, is echoed in the response, and rides every log line
        the request produces. Logged: method, route template, status, ms, pseudonymous actor, reject reason.
        Never logged: query strings, cookies, headers, bodies."""
        t0 = time.monotonic()
        self._status, self._reason, self._actor = 0, None, None
        rid = self.headers.get("X-Request-Id", "")
        self._request_id = rid if _RID.fullmatch(rid) else uuid.uuid4().hex[:16]
        path = urlparse(self.path).path
        with bind(request_id=self._request_id, service="opsview"):
            try:
                fn()
            except Exception as e:
                self._status = self._status or 500
                self._reason = self._reason or "unhandled"
                log.error("request crashed", error=type(e).__name__)
                raise
            finally:
                st = self._status
                (log.warning if st >= 400 else log.info)(
                    "request", method=method, route=route_label(method, path), status=st,
                    ms=round((time.monotonic() - t0) * 1000, 1), actor=self._actor, reason=self._reason)

    # --- plumbing ---------------------------------------------------------
    def _rp(self) -> str:
        return rp_for(self.headers.get("Host", "localhost"))

    def _cookie(self) -> str | None:
        raw = self.headers.get("Cookie", "")
        return next((p.strip().split("=", 1)[1] for p in raw.split(";") if p.strip().startswith("oc_sess=")), None)

    def _cf_cookie(self) -> str | None:
        raw = self.headers.get("Cookie", "")
        return next(
            (p.strip().split("=", 1)[1] for p in raw.split(";") if p.strip().startswith("CF_Authorization=")), None
        )

    _LOOPBACK = {"localhost", "127.0.0.1", "::1"}

    def _identity(self) -> str | None:
        who = self.auth.check_cookie(self._rp(), self._cookie())
        if who is None and self._rp() in self._LOOPBACK:
            self._local_cookie = self.auth.local_session(self._rp())
            who = "local"
        if who is None:
            cf = self._cf_cookie()
            if cf:
                email = access_identity(cf)
                if email:
                    self._local_cookie = self.auth.access_session(self._rp(), email)
                    who = email
                    log.info("access session minted", actor=_actor_label(email))
        self._actor = _actor_label(who) if who else None
        return who

    def _csrf(self) -> str:
        raw = self._cookie() or getattr(self, "_local_cookie", None) or ""
        return hmac.new(CSRF_SECRET, raw.encode(), hashlib.sha256).hexdigest()

    BODY_MAX = 1_000_000
    AUDIO_MAX = 25 * 1024 * 1024

    def _content_length(self) -> int:
        try:
            return max(0, int(self.headers.get("Content-Length", 0) or 0))
        except ValueError:
            return -1

    def _raw_body(self, limit: int) -> bytes | None:
        """Read the body once, never more than `limit`. None = over the limit
        or a bad length; the caller answers 413 without buffering it."""
        if getattr(self, "_raw", None) is None:
            n = self._content_length()
            if n < 0 or n > limit:
                self.close_connection = True  # unread body: don't reuse the socket
                return None
            self._raw = self.rfile.read(n) if n else b""
        return self._raw

    def _body(self) -> dict:
        raw = self._raw_body(self.BODY_MAX) or b""
        if "json" in self.headers.get("Content-Type", "") or raw.lstrip()[:1] == b"{":
            try:
                d = json.loads(raw or b"{}")
                return d if isinstance(d, dict) else {}
            except ValueError:
                return {}
        return {k: v[0] for k, v in parse_qs(raw.decode(errors="replace")).items()}

    def _set_session(self, cookie: str):
        secure = "" if self._rp() in self._LOOPBACK else "; Secure"
        self.send_header(
            "Set-Cookie", f"oc_sess={cookie}; HttpOnly{secure}; SameSite=Strict; Path=/; Max-Age={SESSION_TTL}"
        )

    def _send(
        self, code, body: bytes, ctype="text/html; charset=utf-8", extra: dict | None = None, page=False, csp: str = CSP
    ):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        if page:
            self.send_header("Content-Security-Policy", csp)
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Cache-Control", "no-store")
        local_cookie = getattr(self, "_local_cookie", None)
        if local_cookie:
            self._set_session(local_cookie)
            self._local_cookie = None
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, to: str, code: int = 302):
        self.send_response(code)
        self.send_header("Location", to)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _json(self, code: int, obj: dict):
        self._send(code, json.dumps(obj).encode(), "application/json", {"Cache-Control": "no-store"})

    def _wants_json(self) -> bool:
        return "application/json" in self.headers.get("Accept", "") or "json" in self.headers.get("Content-Type", "")

    def log_message(self, *a):
        pass

    # --- page assembly -----------------------------------------------------
    def _page(self, page: str, body: str, code=200, conn="", sub=""):
        pending = len(ApprovalQueue(approvals_path()).pending())
        self._send(
            code,
            views.shell(
                page, body, csrf=self._csrf(), pending=pending, who=self._identity() or "", conn=conn, sub=sub
            ).encode(),
            page=True,
        )

    # --- GET ---------------------------------------------------------------
    def do_GET(self):
        self._observed("GET", self._do_get)

    def _do_get(self):
        parsed = urlparse(self.path)
        path, qs = parsed.path, parse_qs(parsed.query)

        if path == "/login":
            return self._send(200, login_page().encode())
        if path == "/enroll":
            return self._send(200, enroll_page((qs.get("token") or [""])[0]).encode())
        # Browsers request /favicon.ico by default; the mark is SVG.
        if path in ("/favicon.ico", "/favicon.svg"):
            return self._send(200, (STATIC / "favicon.svg").read_bytes(), "image/svg+xml")
        if workspace.serve_static(self, path):
            return
        if path.startswith("/static/"):
            name = path[len("/static/") :]
            if name not in STATIC_TYPES:
                return self._send(404, b"not found", "text/plain")
            return self._send(
                200, (STATIC / name).read_bytes(), STATIC_TYPES[name] + "; charset=utf-8", {"Cache-Control": "no-cache"}
            )
        if not self._identity():
            self._reason = "unauthenticated"
            return self._redirect("/login")
        if path in REDIRECTS:
            return self._redirect(REDIRECTS[path])
        if workspace.handle_get(self, path, qs):
            return
        if path in POST_ONLY_LEGACY:
            return self._send(
                405, b"This action is POST-only and needs a confirm step.", "text/plain", {"Allow": "POST"}
            )
        name, args = match(GET_ROUTES, path)
        if name is None:
            if match(POST_ROUTES, path)[0]:
                return self._send(405, b"POST only", "text/plain", {"Allow": "POST"})
            return self._page("overview", views.not_found(), 404)
        getattr(self, name)(qs, **args)

    # --- SPA + JSON API (the React front door) ---------------------------
    def g_spa(self, qs):
        index = UI_DIST / "index.html"
        if not index.is_file():
            # Clean checkout / failed build: degrade to the server-rendered
            # console instead of a dead front door. `pnpm --dir ui build` fixes it.
            return self.g_overview(qs)
        return self._send(200, index.read_bytes(), "text/html; charset=utf-8", page=True)

    def g_spa_route(self, qs, **_):
        """A deep link into the React app (/devices, /sandboxes/<id>, ...). The client router
        renders the page; the passkey gate already ran. Unlike `/`, there is no server-rendered
        fallback for these paths, so a missing build is an explicit 503."""
        index = UI_DIST / "index.html"
        if not index.is_file():
            return self._send(
                503, b"The console UI is not built. Run: pnpm --dir ui build", "text/plain; charset=utf-8"
            )
        return self._send(200, index.read_bytes(), "text/html; charset=utf-8", page=True)

    def g_asset(self, qs, file):
        name = Path(file).name  # no traversal
        p = UI_DIST / "assets" / name
        if not p.is_file():
            return self._send(404, b"not found", "text/plain")
        ctype = _ASSET_TYPES.get(p.suffix, "application/octet-stream")
        return self._send(200, p.read_bytes(), ctype, {"Cache-Control": "public, max-age=31536000, immutable"})

    def g_api_session(self, qs):
        self._json(200, {"csrf": self._csrf(), "identity": self._identity() or "", "cap": daily_cap()})

    def g_api_runs(self, qs):
        self._json(200, {"runs": goalruns.list_runs()})

    def g_api_approvals(self, qs):
        items = ApprovalQueue(approvals_path()).items()
        for it in items:
            it["age_s"] = round(time.time() - it["ts"], 1)
        self._json(200, {"approvals": items})

    def p_api_approve(self, who, body, id):
        self._decide(who, body, id, True)

    def p_api_sendback(self, who, body, id):
        self._decide(who, body, id, False)

    def g_api_run(self, qs, id):
        r = goalruns.get_run(id)
        if r is None:
            return self._json(404, {"error": "no such run"})
        self._json(200, r)

    def g_api_agent_jobs(self, qs):
        self._json(200, {"jobs": agentjobs.list_jobs()})

    def g_api_agent_job(self, qs, id):
        j = agentjobs.get_job(id)
        if j is None:
            return self._json(404, {"error": "no such job"})
        self._json(200, j)

    def p_transcribe(self, who, body):
        """Voice → text on Groq LPU (whisper-large-v3-turbo). The browser posts
        a multipart audio body; the Groq key lives in the process env (injected
        by the oc-opsview wrapper from omaseal), never on disk."""
        key = os.environ.get("GROQ_API_KEY", "")
        if not key:
            return self._json(503, {"error": "voice not configured (no GROQ_API_KEY)"})
        # Session + Origin + header CSRF were checked in do_POST before this
        # read — only then is the larger audio budget spent.
        audio = self._raw_body(self.AUDIO_MAX)
        if audio is None:
            return self._json(413, {"error": "Recording too long (max 25 MB)."})
        if not audio:
            return self._json(400, {"error": "No audio received."})
        ctype = self.headers.get("Content-Type", "")
        if not ctype.startswith("multipart/form-data"):
            return self._json(415, {"error": "Expected multipart/form-data audio."})
        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            data=audio,
            method="POST",
            headers={
                "Content-Type": ctype,
                "Authorization": f"Bearer {key}",
                # Groq's Cloudflare edge rejects the default Python urllib UA (1010).
                "User-Agent": "open-computer/0.1",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                out = r.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:200]
            return self._json(502, {"error": f"groq error {e.code}: {detail}"})
        except OSError as e:
            return self._json(502, {"error": f"groq unreachable: {type(e).__name__}"})
        self._send(200, out, "application/json", {"Cache-Control": "no-store"})

    def g_work(self, qs):
        self._page("work", views.page_work(self._csrf()), sub="Goals, plans, results — the workplace")

    def g_overview(self, qs):
        now = time.time()
        q = ApprovalQueue(approvals_path())
        audit = list(reversed(read_ndjson(audit_path())))[:8]
        body = views.page_overview(q.pending(), runstore.list_runs(), audit, day_spend(), daily_cap(), now)
        self._page("overview", body, sub="What needs you, and what is running")

    def g_api_overview(self, qs):
        q = ApprovalQueue(approvals_path())
        audit = list(reversed(read_ndjson(audit_path())))[:8]
        self._json(
            200,
            {
                "pending_approvals": q.pending(),
                "runs": runstore.list_runs(),
                "audit": audit,
                "spend": day_spend(),
                "cap": daily_cap(),
                "now": time.time(),
            },
        )

    def g_api_sandboxes(self, qs):
        self._json(200, consoleapi.sandboxes_payload(fleet_state()))

    def g_api_devices(self, qs):
        self._json(200, devices.collect_devices(oc_root()))

    def g_goals(self, qs):
        self._page(
            "goals",
            views.page_goals(goalruns.list_runs(), time.time()),
            sub='<a class="btn primary" href="/goals/new" data-action="goal.new">New goal</a>',
        )

    def g_goal_new(self, qs):
        self._page("goals", views.page_goal_new(self._csrf()), sub="Decomposed into subtasks before it runs")

    def g_goal(self, qs, id):
        run = goalruns.get_run(id)
        if not run:
            return self._page("goals", views.not_found(), 404)
        self._page("goals", views.page_goal_detail(run, time.time()), sub=id)

    def g_runs(self, qs):
        self._page(
            "runs",
            views.page_runs(runstore.list_runs(), time.time()),
            sub='<a class="btn primary" href="/runs/new" data-action="run.new">New matrix run</a>',
        )

    def g_run_new(self, qs):
        self._page("runs", views.page_run_confirm(self._csrf(), fleet_state()), sub="Confirm before anything boots")

    def g_run(self, qs, id):
        got = runstore.get_run(id)
        if not got:
            return self._page("runs", views.not_found(), 404)
        self._page("runs", views.page_run_detail(got[0], got[1], time.time()), sub=got[0]["id"])

    def g_approvals(self, qs):
        items = ApprovalQueue(approvals_path()).items()
        self._page("approvals", views.page_approvals(items, time.time()), sub="Oldest first")

    def g_skills(self, qs):
        self._page(
            "skills",
            views.page_skills(capabilities.collect()),
            sub="Capability surface — live health, never aspiration",
        )

    def g_automations(self, qs):
        self._page(
            "automations",
            views.page_automations(self._csrf(), automations.list_automations(), time.time()),
            sub="Goals on a cadence — durable Temporal schedules",
        )

    def g_api_router(self, qs):
        self._json(
            200,
            consoleapi.router_payload(
                read_ndjson(ledger_path()), day_spend(), daily_cap(), paid_router(), views.host_of(router_base())
            ),
        )

    def g_api_audit(self, qs):
        f, q = (qs.get("f") or ["all"])[0], (qs.get("q") or [""])[0]
        self._json(200, consoleapi.audit_payload(read_ndjson(audit_path()), f, q))

    def g_frag_status(self, qs):
        self._send(200, views.status_cell(fleet_state()).encode(), page=True)

    def g_frag_work(self, qs):
        rid = (qs.get("run") or [""])[0]
        sel = (qs.get("sub") or [""])[0]
        run = goalruns.get_run(rid) if re.fullmatch(r"goal_[0-9a-f]{8}", rid) else None
        pend = ApprovalQueue(approvals_path()).pending()
        runs = goalruns.list_runs()
        now = time.time()
        payload = {
            "rail": views.work_rail(runs, now, rid if run else ""),
            "main": views.work_main(run, now, sel),
            "inspect": views.work_inspect(run, sel, pend, now),
            "active": views.work_active(runs, run),
        }
        self._send(200, json.dumps(payload).encode(), "application/json")

    # --- POST --------------------------------------------------------------
    def do_POST(self):
        self._observed("POST", self._do_post)

    def _do_post(self):
        self._raw = None  # never reuse a previous request's body
        path = urlparse(self.path).path
        if path.startswith("/webauthn/"):
            return self._webauthn(path)
        who = self._identity()
        if not who:
            self._reason = "unauthenticated"
            return self._json(401, {"error": "Sign in again."})
        name, args = match(POST_ROUTES, path)
        if name is None and not workspace.is_ws_path(path):
            return self._json(404, {"error": "not found"})
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc != self.headers.get("Host", ""):
            self._reason = "cross_origin"
            return self._json(403, {"error": "Cross-origin request refused."})
        if name == "p_transcribe":
            # Binary upload: CSRF must come from the header, checked before
            # the (larger) body is read at all.
            body, token = {}, self.headers.get("X-CSRF-Token", "")
        else:
            if self._raw_body(self.BODY_MAX) is None:
                self._reason = "body_too_large"
                return self._json(413, {"error": "Request body too large (max 1 MB)."})
            body = self._body()
            token = self.headers.get("X-CSRF-Token") or str(body.get("csrf", ""))
        if not hmac.compare_digest(token, self._csrf()):
            self._reason = "csrf"
            return self._json(403, {"error": "Missing or invalid CSRF token. Reload the page and try again."})
        if name is None:
            return workspace.handle_post(self, who, body, path)
        getattr(self, name)(who, body, **args)

    def p_runs(self, who, body):
        if body.get("confirm") != "1":
            return self._json(400, {"error": "Confirm the run first."})
        t0 = time.time()
        out = _run_matrix()
        row = runstore.record(out, by=who, duration_s=time.time() - t0)
        self._redirect(f"/runs/{row['id']}", 303)

    def p_goals(self, who, body):
        goal = str(body.get("goal", "")).strip()
        if not goal or len(goal) > 4000:
            if self._wants_json():
                return self._json(400, {"error": "A goal is required (max 4000 chars)."})
            return self._page("goals", views.page_goal_new(self._csrf(), "A goal is required (max 4000 chars)."), 400)
        if body.get("confirm") != "1":
            if self._wants_json():
                return self._json(200, {"needs_confirm": True, "cap": daily_cap()})
            return self._page(
                "goals", views.page_goal_confirm(self._csrf(), goal, daily_cap()), sub="Confirm before it runs"
            )
        try:
            rid = _submit_goal(goal, who)
        except Exception as e:
            if self._wants_json():
                return self._json(500, {"error": f"{type(e).__name__}: {e}"})
            return self._page("goals", views.page_goal_new(self._csrf(), f"{type(e).__name__}: {e}"), 500)
        if self._wants_json():
            return self._json(200, {"run": rid})
        self._redirect(f"/goals/{rid}", 303)


    def p_agent_submit(self, who, body):
        goal = body.get("goal")
        goal = goal.strip() if isinstance(goal, str) else ""
        if not goal or len(goal) > 4000:
            return self._json(422, {"error": "A goal is required (max 4000 chars)."})
        try:
            if body.get("confirm") != "1":
                return self._json(200, {"needs_confirm": True, **agentjobs.submit_defaults(body.get("budget"))})
            return self._json(200, {"job": agentjobs.submit(goal, body.get("budget"), who)})
        except ValueError as e:
            return self._json(422, {"error": str(e)})
        except Exception as e:
            return self._json(500, {"error": f"{type(e).__name__}: {e}"})

    def _agent_decide(self, who, body, id, aid, approve: bool):
        try:
            agentjobs.decide(id, aid, approve, who, body.get("hash"), str(body.get("note", "")))
        except ApprovalError as e:
            return self._json(e.status, {"error": str(e)})
        except Exception as e:  # a grant that could not be written: the request stays pending
            return self._json(500, {"error": str(e) if isinstance(e, GrantError) else type(e).__name__})
        self._json(200, {"ok": True})

    def p_agent_approve(self, who, body, id, aid):
        self._agent_decide(who, body, id, aid, True)

    def p_agent_deny(self, who, body, id, aid):
        self._agent_decide(who, body, id, aid, False)

    def p_api_router_test(self, who, body):
        q = str(body.get("q", "")).strip()[:500]
        if not q:
            return self._json(400, {"error": "Enter something to route."})
        if paid_router() and body.get("confirm") != "1":
            # Leaving the box costs money: say where and how much, send nothing yet.
            return self._json(
                200,
                {
                    "needs_confirm": True,
                    "dest_host": views.host_of(router_base()),
                    "cost": consoleapi.PAID_ROUTE_COST,
                },
            )
        try:
            result = _route(q)
        except Exception as e:  # BudgetHalt, engine failure
            return self._json(502, {"error": f"Routing failed: {e}. Nothing was sent."})
        self._json(200, {"result": result})

    def _decide(self, who, body, id, approve: bool):
        gate = Gate(audit_path())
        try:
            res = ApprovalQueue(approvals_path(), gate).decide(
                id,
                approve=approve,
                by=who,
                note=str(body.get("note", "")),
                expect_hash=str(body["hash"]) if body.get("hash") else None,
            )
        except QueueError as e:
            if self._wants_json():
                return self._json(e.status, {"error": str(e)})
            return self._page("approvals", f'<p class="err" role="alert">{views.e(str(e))}</p>', e.status)
        if self._wants_json():
            return self._json(200, {"ok": True, **res})
        self._redirect("/approvals", 303)

    def p_approve(self, who, body, id):
        self._decide(who, body, id, True)

    def p_sendback(self, who, body, id):
        self._decide(who, body, id, False)

    def _auto_body(self, error: str = "") -> str:
        return views.page_automations(self._csrf(), automations.list_automations(), time.time(), error)

    def p_automations(self, who, body):
        goal = str(body.get("goal", "")).strip()
        try:
            every = int(body.get("every_minutes", ""))
        except (TypeError, ValueError):
            every = 0
        if not goal or len(goal) > 4000 or not 5 <= every <= 10080:
            return self._page(
                "automations", self._auto_body("A goal and a cadence of 5–10080 minutes are required."), 400
            )
        try:
            automations.create(goal, every)
        except Exception as e:
            return self._page("automations", self._auto_body(f"{type(e).__name__}: {e}"), 500)
        self._redirect("/automations", 303)

    def p_auto_pause(self, who, body, id):
        try:
            automations.set_paused(id, str(body.get("paused")) == "1")
        except Exception as e:
            return self._page("automations", self._auto_body(f"{type(e).__name__}: {e}"), 500)
        self._redirect("/automations", 303)

    def _auto_confirm(self, body, id, op) -> bool:
        """Spendy/destructive ops need the confirm step — names the schedule."""
        if body.get("confirm") == "1":
            return True
        desc = str(body.get("desc", ""))[:200]
        self._page(
            "automations", views.page_automation_confirm(self._csrf(), id, op, desc), sub="Confirm before it acts"
        )
        return False

    def p_auto_trigger(self, who, body, id):
        if not self._auto_confirm(body, id, "trigger"):
            return
        try:
            automations.trigger(id)
        except Exception as e:
            return self._page("automations", self._auto_body(f"{type(e).__name__}: {e}"), 500)
        self._redirect("/automations", 303)

    def p_auto_delete(self, who, body, id):
        if not self._auto_confirm(body, id, "delete"):
            return
        try:
            automations.delete(id)
        except Exception as e:
            return self._page("automations", self._auto_body(f"{type(e).__name__}: {e}"), 500)
        self._redirect("/automations", 303)

    def _webauthn(self, path):
        body = self._body()
        rp = self._rp()
        if path == "/webauthn/login/begin":
            opts, tok = self.auth.begin_auth(rp)
            return self._json(200, {"publicKey": opts["publicKey"], "tok": tok})
        if path == "/webauthn/login/complete":
            try:
                got = self.auth.complete_auth(body.get("tok", ""), json.dumps(body.get("credential", {})))
            except Exception:
                got = None
            if not got:
                self._reason = "passkey_denied"
                return self._json(401, {"error": "denied"})
            log.info("passkey login ok")
            label, cookie = got
            self.send_response(200)
            self._set_session(cookie)
            self.send_header("Content-Type", "application/json")
            payload = json.dumps({"ok": True, "who": label}).encode()
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if path == "/webauthn/register/begin":
            if not self.auth.consume_token(rp, body.get("token", "")):
                self._reason = "bad_enroll_token"
                return self._json(403, {"error": "invalid or expired enroll token"})
            opts, tok = self.auth.begin_register(rp, "luke")
            return self._json(200, {"publicKey": opts["publicKey"], "tok": tok})
        if path == "/webauthn/register/complete":
            try:
                cred_id = self.auth.complete_register(body.get("tok", ""), json.dumps(body.get("credential", {})))
            except Exception:
                cred_id = None
            if not cred_id:
                self._reason = "enroll_failed"
                return self._json(401, {"error": "enrollment failed"})
            log.info("passkey enrolled")
            return self._json(200, {"ok": True})
        self._json(404, {"error": "not found"})


def make_server(port: int, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), Handler)


def main(port: int = 8791):
    log.info("console listening", host="127.0.0.1", port=port)
    _resume_goals()
    _resume_agent_jobs()
    make_server(port).serve_forever()


if __name__ == "__main__":
    from services.telemetry import install_excepthook, report_failure

    install_excepthook()
    try:
        main(int(sys.argv[1]) if len(sys.argv) > 1 else 8791)
    except Exception as e:
        report_failure(f"opsview exited: {type(e).__name__}: {e}", exc=e, severity="fatal")
        raise
