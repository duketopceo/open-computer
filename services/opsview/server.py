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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from services.gate.gate import Gate
from services.gate.queue import ApprovalQueue, QueueError
from services.opsview import goalruns
from services.opsview import runs as runstore
from services.opsview import views
from services.opsview.auth import Auth, SESSION_TTL, rp_for
from services.opsview.webauthn_page import enroll_page, login_page
from services.orchestrator import engines as goal_engines

REPO = Path(__file__).resolve().parents[2]
STATIC = Path(__file__).resolve().parent / "static"
STATIC_TYPES = {
    "silver-lining.css": "text/css", "console.css": "text/css",
    "console.js": "text/javascript", "theme-init.js": "text/javascript",
    "work.js": "text/javascript",
}
CSP = ("default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self' data:; "
       "font-src 'self'; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
CSRF_SECRET = secrets.token_bytes(32)
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
    except Exception:
        return None


# ------------------------------------------------------------------ config


def ledger_path() -> Path:
    return Path(os.environ.get("OC_ROUTER_LEDGER", "/tmp/oc-audit/router-ledger.jsonl"))


def audit_path() -> Path:
    return Path(os.environ.get("OC_AUDIT", "/tmp/oc-audit/audit.ndjson"))


def approvals_path() -> Path:
    return Path(os.environ.get("OC_APPROVALS", "/tmp/oc-audit/approvals.ndjson"))


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
        st["sandboxes"] = data if isinstance(data, list) else []
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
        float(r.get("cost", 0) or 0) for r in read_ndjson(ledger_path())
        if time.strftime("%Y-%m-%d", time.localtime(r.get("ts", 0))) == today
    )


# --------------------------------------------------------- mutation paths


def _run_matrix() -> str:
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    try:
        p = subprocess.run(
            ["uv", "run", "--with", "pytest", "--with", "fido2",
             "--with-editable", str(Path.home() / "src/cubesandbox/sdk/python"),
             "pytest", "tests/conformance", "-q"],
            cwd=REPO, env=env, capture_output=True, text=True, timeout=180,
        )
        return p.stdout + p.stderr
    except Exception as e:
        return f"matrix run failed: {e}"


def _route(q: str) -> dict:
    sys.path.insert(0, str(REPO))
    from services.router import default_router

    r = default_router(str(ledger_path()), daily_cap=daily_cap())
    d = r.route(q)
    return {"route": d.route, "tier": d.tier, "engine": d.engine, "confidence": d.confidence,
            "cost": d.cost, "ms": round(d.ms * 1000) if d.ms < 10 else round(d.ms)}


def _submit_goal(goal: str, who: str) -> str:
    """Spending path behind POST /goals confirm. Tests patch this."""
    return goalruns.submit(goal, goal_engines.decompose_engine(), goal_engines.work_fn(), by=who)


def _resume_goals() -> None:
    """Re-drive goal runs orphaned by a restart. Fails soft — disk is truth."""
    try:
        n = goalruns.resume(goal_engines.work_fn())
        if n:
            print(f"resumed {n} goal run(s)")
    except Exception:
        pass


# ----------------------------------------------------------------- routes
# (pattern, method name). The tests enumerate these tables to prove GETs are
# side-effect free and that every POST is CSRF-protected.

GET_ROUTES = [
    (r"/", "g_overview"), (r"/work", "g_work"),
    (r"/sandboxes", "g_sandboxes"), (r"/sandboxes/(?P<id>[^/]+)", "g_sandbox"),
    (r"/goals", "g_goals"), (r"/goals/new", "g_goal_new"), (r"/goals/(?P<id>goal_[0-9a-f]{8})", "g_goal"),
    (r"/runs", "g_runs"), (r"/runs/new", "g_run_new"), (r"/runs/(?P<id>run_[0-9a-f]{8})", "g_run"),
    (r"/approvals", "g_approvals"), (r"/router", "g_router"), (r"/audit", "g_audit"),
    (r"/_frag/fleet", "g_frag_fleet"), (r"/_frag/status", "g_frag_status"), (r"/_frag/work", "g_frag_work"),
]
REDIRECTS = {"/fleet": "/sandboxes", "/matrix": "/runs", "/gate": "/approvals", "/ops": "/"}
POST_ROUTES = [
    (r"/runs", "p_runs"), (r"/goals", "p_goals"), (r"/router/test", "p_router_test"),
    (r"/approvals/(?P<id>ap_[0-9a-f]{8})/approve", "p_approve"),
    (r"/approvals/(?P<id>ap_[0-9a-f]{8})/send-back", "p_sendback"),
]
# Old side-effecting GET URLs now answer 405 instead of acting.
POST_ONLY_LEGACY = {"/run", "/route"}


def match(table, path):
    for pat, name in table:
        m = re.fullmatch(pat, path)
        if m:
            return name, m.groupdict()
    return None, {}


class Handler(BaseHTTPRequestHandler):
    auth = Auth()

    # --- plumbing ---------------------------------------------------------
    def _rp(self) -> str:
        return rp_for(self.headers.get("Host", "localhost"))

    def _cookie(self) -> str | None:
        raw = self.headers.get("Cookie", "")
        return next((p.strip().split("=", 1)[1] for p in raw.split(";") if p.strip().startswith("oc_sess=")), None)

    def _cf_cookie(self) -> str | None:
        raw = self.headers.get("Cookie", "")
        return next((p.strip().split("=", 1)[1] for p in raw.split(";") if p.strip().startswith("CF_Authorization=")), None)

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
        return who

    def _csrf(self) -> str:
        raw = self._cookie() or getattr(self, "_local_cookie", None) or ""
        return hmac.new(CSRF_SECRET, raw.encode(), hashlib.sha256).hexdigest()

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(min(n, 1_000_000)) if n else b""
        if "json" in self.headers.get("Content-Type", "") or raw.lstrip()[:1] == b"{":
            try:
                d = json.loads(raw or b"{}")
                return d if isinstance(d, dict) else {}
            except ValueError:
                return {}
        return {k: v[0] for k, v in parse_qs(raw.decode(errors="replace")).items()}

    def _set_session(self, cookie: str):
        secure = "" if self._rp() in self._LOOPBACK else "; Secure"
        self.send_header("Set-Cookie", f"oc_sess={cookie}; HttpOnly{secure}; SameSite=Strict; Path=/; Max-Age={SESSION_TTL}")

    def _send(self, code, body: bytes, ctype="text/html; charset=utf-8", extra: dict | None = None, page=False):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        if page:
            self.send_header("Content-Security-Policy", CSP)
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
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _wants_json(self) -> bool:
        return "application/json" in self.headers.get("Accept", "") or "json" in self.headers.get("Content-Type", "")

    def log_message(self, *a):
        pass

    # --- page assembly -----------------------------------------------------
    def _page(self, page: str, body: str, code=200, conn="", sub=""):
        pending = len(ApprovalQueue(approvals_path()).pending())
        self._send(code, views.shell(page, body, csrf=self._csrf(), pending=pending,
                                     who=self._identity() or "", conn=conn, sub=sub).encode(), page=True)

    # --- GET ---------------------------------------------------------------
    def do_GET(self):
        parsed = urlparse(self.path)
        path, qs = parsed.path, parse_qs(parsed.query)

        if path == "/login":
            return self._send(200, login_page().encode())
        if path == "/enroll":
            return self._send(200, enroll_page((qs.get("token") or [""])[0]).encode())
        if path.startswith("/static/"):
            name = path[len("/static/"):]
            if name not in STATIC_TYPES:
                return self._send(404, b"not found", "text/plain")
            return self._send(200, (STATIC / name).read_bytes(), STATIC_TYPES[name] + "; charset=utf-8",
                              {"Cache-Control": "no-cache"})
        if not self._identity():
            return self._redirect("/login")
        if path in REDIRECTS:
            return self._redirect(REDIRECTS[path])
        if path in POST_ONLY_LEGACY:
            return self._send(405, b"This action is POST-only and needs a confirm step.", "text/plain",
                              {"Allow": "POST"})
        name, args = match(GET_ROUTES, path)
        if name is None:
            if match(POST_ROUTES, path)[0]:
                return self._send(405, b"POST only", "text/plain", {"Allow": "POST"})
            return self._page("overview", views.not_found(), 404)
        getattr(self, name)(qs, **args)

    def g_work(self, qs):
        self._page("work", views.page_work(self._csrf()), sub="Goals, plans, results — the workplace")

    def g_overview(self, qs):
        now = time.time()
        q = ApprovalQueue(approvals_path())
        audit = list(reversed(read_ndjson(audit_path())))[:8]
        body = views.page_overview(q.pending(), runstore.list_runs(), audit, day_spend(), daily_cap(), now)
        self._page("overview", body, sub="What needs you, and what is running")

    def g_sandboxes(self, qs):
        inline = (qs.get("inline") or [""])[0] == "1"
        fleet = fleet_state() if inline else {"configured": bool(os.environ.get("CUBE_API_URL")), "up": False,
                                              "sandboxes": [], "api_host": views.host_of(os.environ.get("CUBE_API_URL", ""))}
        self._page("sandboxes", views.page_sandboxes(fleet, inline), conn=views.conn_pill(fleet) if inline else "")

    def g_sandbox(self, qs, id):
        sid = unquote(id)
        row = next((s for s in fleet_state()["sandboxes"] if str(s.get("sandboxID")) == sid), None)
        self._page("sandboxes", views.page_sandbox_detail(sid, row), 200 if row else 404, sub=views.e(sid))

    def g_goals(self, qs):
        self._page("goals", views.page_goals(goalruns.list_runs(), time.time()),
                   sub='<a class="btn primary" href="/goals/new" data-action="goal.new">New goal</a>')

    def g_goal_new(self, qs):
        self._page("goals", views.page_goal_new(self._csrf()), sub="Decomposed into subtasks before it runs")

    def g_goal(self, qs, id):
        run = goalruns.get_run(id)
        if not run:
            return self._page("goals", views.not_found(), 404)
        self._page("goals", views.page_goal_detail(run, time.time()), sub=id)

    def g_runs(self, qs):
        self._page("runs", views.page_runs(runstore.list_runs(), time.time()),
                   sub='<a class="btn primary" href="/runs/new" data-action="run.new">New matrix run</a>')

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

    def g_router(self, qs):
        self._page("router", self._router_body(), sub="Route tester and decision ledger")

    def _router_body(self, result: str = "") -> str:
        return views.page_router(self._csrf(), read_ndjson(ledger_path())[-14:], day_spend(), daily_cap(),
                                 paid_router(), router_base(), time.time(), result)

    def g_audit(self, qs):
        f = (qs.get("f") or ["all"])[0]
        f = f if f in dict(views.AUDIT_FILTERS) else "all"
        q = (qs.get("q") or [""])[0][:200]
        rows = [r for r in read_ndjson(audit_path()) if views.audit_match(r, f, q)]
        self._page("audit", views.page_audit(list(reversed(rows))[:200], len(rows), f, q, time.time()),
                   sub="Append-only")

    def g_frag_fleet(self, qs):
        self._send(200, views.fleet_rows(fleet_state()).encode(), page=True)

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
            "main": views.work_main(run, now),
            "inspect": views.work_inspect(run, sel, pend, now),
            "active": views.work_active(runs, run),
        }
        self._send(200, json.dumps(payload).encode(), "application/json")

    # --- POST --------------------------------------------------------------
    def do_POST(self):
        path = urlparse(self.path).path
        if path.startswith("/webauthn/"):
            return self._webauthn(path)
        who = self._identity()
        if not who:
            return self._json(401, {"error": "Sign in again."})
        name, args = match(POST_ROUTES, path)
        if name is None:
            return self._json(404, {"error": "not found"})
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc != self.headers.get("Host", ""):
            return self._json(403, {"error": "Cross-origin request refused."})
        body = self._body()
        token = self.headers.get("X-CSRF-Token") or str(body.get("csrf", ""))
        if not hmac.compare_digest(token, self._csrf()):
            return self._json(403, {"error": "Missing or invalid CSRF token. Reload the page and try again."})
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
            return self._page("goals", views.page_goal_confirm(self._csrf(), goal, daily_cap()),
                              sub="Confirm before it runs")
        rid = _submit_goal(goal, who)
        if self._wants_json():
            return self._json(200, {"run": rid})
        self._redirect(f"/goals/{rid}", 303)

    def p_router_test(self, who, body):
        q = str(body.get("q", "")).strip()[:500]
        if not q:
            return self._page("router", self._router_body(), 400)
        if paid_router() and body.get("confirm") != "1":
            return self._page("router", views.router_confirm(self._csrf(), q, router_base()), sub="Confirm before it leaves")
        try:
            result = views.route_result(_route(q))
        except Exception as e:  # BudgetHalt, engine failure
            result = f'<p class="err" role="alert">Routing failed: {views.e(str(e))}. Nothing was sent.</p>'
        self._page("router", self._router_body(result), sub="Route tester and decision ledger")

    def _decide(self, who, body, id, approve: bool):
        gate = Gate(audit_path())
        try:
            res = ApprovalQueue(approvals_path(), gate).decide(
                id, approve=approve, by=who, note=str(body.get("note", "")),
                expect_hash=str(body["hash"]) if body.get("hash") else None)
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
                return self._json(401, {"error": "denied"})
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
                return self._json(403, {"error": "invalid or expired enroll token"})
            opts, tok = self.auth.begin_register(rp, "luke")
            return self._json(200, {"publicKey": opts["publicKey"], "tok": tok})
        if path == "/webauthn/register/complete":
            try:
                cred_id = self.auth.complete_register(body.get("tok", ""), json.dumps(body.get("credential", {})))
            except Exception:
                cred_id = None
            if not cred_id:
                return self._json(401, {"error": "enrollment failed"})
            return self._json(200, {"ok": True})
        self._json(404, {"error": "not found"})


def make_server(port: int, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), Handler)


def main(port: int = 8791):
    print(f"console → http://127.0.0.1:{port}")
    _resume_goals()
    make_server(port).serve_forever()


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8791)
