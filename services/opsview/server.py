"""open-computer workplace — rail + pane shell over real-data views.

stdlib only. Every number is live (probed on request or on matrix run);
nothing is stubbed. States render explicitly as words.

Views: / (work — task rail, ops body until U6), /matrix, /fleet, /gate,
/router, /ops (legacy standalone page). Gate: passkey (auth.py).

Run:  CUBE_API_URL=... CUBE_API_KEY=... python -m services.opsview.server
Keys: g+w/g/m/f/r/o views · ? help · / palette · r run matrix (matrix view)
"""

from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from services.opsview.auth import Auth, rp_for
from services.opsview.webauthn_page import enroll_page, login_page

REPO = Path(__file__).resolve().parents[2]
MATRIX_RE = re.compile(r"^(R\d+|ROUTER|AUTH|PLACE|ORCH): (PASS|FAIL|PENDING) \((\d+) checks\)", re.M)
LEDGER_PATH = Path(os.environ.get("OC_ROUTER_LEDGER", "/tmp/oc-audit/router-ledger.jsonl"))

CSS = """
:root{color-scheme:dark;--bg:#0b0e11;--fg:#d7dde3;--dim:#7d8a96;--ok:#3fb96f;
--bad:#e05d5d;--pend:#c9a227;--line:#1d242b;--acc:#4d9fff}
*{box-sizing:border-box;margin:0}
body{background:var(--bg);color:var(--fg);font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;
display:flex;min-height:100vh}
aside{width:210px;min-width:210px;border-right:1px solid var(--line);padding:16px 14px;
display:flex;flex-direction:column;gap:2px}
main{flex:1;padding:20px 24px;max-width:1100px}
.brand{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--dim);
margin-bottom:14px}
.sect{font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--dim);
margin:14px 0 4px}
.nav a{display:flex;justify-content:space-between;color:var(--fg);text-decoration:none;
padding:3px 8px;border:1px solid transparent;border-radius:3px}
.nav a:hover{border-color:var(--line)}
.nav a.on{background:#141a20;border-color:var(--line)}
.nav a .k{color:var(--dim)}
.tl{color:var(--dim);padding:3px 8px;font-size:12px}
h1{font-size:14px;letter-spacing:.12em;text-transform:uppercase;color:var(--dim);
margin-bottom:2px}
.sub{color:var(--dim);margin-bottom:18px}
h2{font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:var(--dim);
margin:18px 0 6px;border-bottom:1px solid var(--line);padding-bottom:4px}
table{border-collapse:collapse;width:100%}
td,th{padding:3px 10px 3px 0;text-align:left;vertical-align:top;white-space:nowrap}
th{color:var(--dim);font-weight:normal}
td:last-child,th:last-child{white-space:normal}
.pass{color:var(--ok)}.fail{color:var(--bad)}.pending{color:var(--pend)}
.dim{color:var(--dim)}
button{background:#141a20;color:var(--fg);border:1px solid var(--line);
font:inherit;padding:4px 14px;cursor:pointer}
button:hover{border-color:var(--acc);color:var(--acc)}
button:disabled{opacity:.4;cursor:default}
input{background:#141a20;border:1px solid var(--line);color:var(--fg);font:inherit;
padding:4px 10px;width:340px}
#status{white-space:pre;color:var(--dim);margin-top:8px}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px}
.up{background:var(--ok)}.down{background:var(--bad)}
a{color:var(--acc);text-decoration:none}
#pal{position:fixed;inset:0;background:rgba(0,0,0,.55);display:none;align-items:flex-start;
justify-content:center;padding-top:18vh}
#pal.on{display:flex}
#pal .box{background:var(--bg);border:1px solid var(--line);min-width:380px}
#pal .box a{display:block;padding:6px 14px;color:var(--fg)}
#pal .box a:hover{background:#141a20}
#pal .box .h{color:var(--dim);font-size:11px;letter-spacing:.12em;text-transform:uppercase;
padding:6px 14px 2px}
#help{position:fixed;right:14px;bottom:12px;color:var(--dim)}
.chip{display:inline-block;font-size:10px;letter-spacing:.1em;padding:1px 8px;
border:1px solid var(--line);border-radius:3px;min-width:76px;text-align:center}
.st-QUEUED{color:var(--dim)}
.st-LEASED,.st-RUNNING{color:var(--pend);border-color:#c9a22755;background:#c9a22712}
.st-DONE{color:var(--ok);border-color:#3fb96f55;background:#3fb96f12}
.st-FAILED{color:var(--bad);border-color:#e05d5d55;background:#e05d5d12}
.st-AWAITING-APPROVAL{color:var(--acc);border-color:#4d9fff55;background:#4d9fff12}
.goalbar{display:flex;gap:8px;margin:6px 0 18px}
.goalbar input{flex:1;max-width:560px}
.runrow{display:block;color:var(--fg);padding:3px 8px;border-radius:3px}
.runrow:hover{background:#141a20}
.runrow .g{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:170px}
.runrow .s{color:var(--dim);font-size:11px}
.subtask{border:1px solid var(--line);border-radius:6px;padding:8px 12px;margin:8px 0}
.subtask .head{display:flex;justify-content:space-between;gap:10px}
.subtask .meta{color:var(--dim);font-size:11px;margin-top:4px}
.subtask .result{margin-top:6px;padding-top:6px;border-top:1px dashed var(--line);
white-space:pre-wrap;font-size:12px;color:var(--fg)}
"""

VIEWS = [
    ("/", "work", "w"),
    ("/gate", "gate", "g"),
    ("/matrix", "matrix", "m"),
    ("/fleet", "fleet", "f"),
    ("/router", "router", "r"),
    ("/ops", "ops", "o"),
]


def _get(url: str, key: str, timeout: float = 3.0) -> tuple[int, str]:
    req = urllib.request.Request(url)
    if key:
        req.add_header("X-API-Key", key)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode()
    except Exception as e:
        return 0, str(e)


def _run_matrix() -> str:
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    try:
        p = subprocess.run(
            [
                "uv", "run", "--with", "pytest", "--with", "fido2",
                "--with-editable", str(Path.home() / "src/cubesandbox/sdk/python"),
                "pytest", "tests/conformance", "-q",
            ],
            cwd=REPO, env=env, capture_output=True, text=True, timeout=180,
        )
        return p.stdout + p.stderr
    except Exception as e:
        return f"matrix run failed: {e}"


def _route(q: str) -> dict:
    sys.path.insert(0, str(REPO))
    from services.router import default_router

    r = default_router("/tmp/oc-audit/router-ledger.jsonl")
    t0 = time.time()
    d = r.route(q)
    return {
        "route": d.route, "tier": d.tier, "engine": d.engine,
        "confidence": d.confidence, "cost": d.cost,
        "ms": round(d.ms * 1000) if d.ms < 10 else round(d.ms),
    }


def _matrix_table(matrix: dict) -> str:
    rows = "".join(
        f'<tr><td>{rule}</td><td class="{st.lower()}">{st}</td><td class="dim">{n} checks</td></tr>'
        for rule, (st, n) in sorted(
            matrix.items(), key=lambda kv: (0, int(kv[0][1:])) if kv[0][1:].isdigit() else (1, kv[0])
        )
    )
    return f"<table>{rows}</table>" if rows else '<span class="dim">not run — press r or Run matrix</span>'


def _sec_matrix(state: dict) -> str:
    return f"""<h2>determinism matrix</h2>
<button id=run>Run matrix&nbsp;<span class=dim>[r]</span></button>
<div id=status></div>
<div id=matrix>{_matrix_table(state["matrix"])}</div>"""


def _sec_fleet(state: dict) -> str:
    sb = state["sandboxes"]
    if state["health"] != 200:
        return '<h2>fleet</h2><span class="dim">control plane DOWN — inventory unknown</span>'
    rows = "".join(
        f'<tr><td>{html.escape(str(s.get("sandboxID", "?"))[:12])}</td>'
        f'<td>{html.escape(str(s.get("templateID", "?"))[:16])}</td>'
        f'<td>{html.escape(str(s.get("status", "?")))}</td></tr>'
        for s in sb
    )
    return (
        f"<h2>fleet ({len(sb)})</h2><table><tr><th>sandbox</th><th>template</th><th>status</th></tr>{rows}</table>"
        if rows
        else "<h2>fleet (0)</h2><span class=dim>none running</span>"
    )


def _sec_gate(state: dict) -> str:
    rows = "".join(
        f'<tr><td class="dim">{html.escape(str(r.get("ts", ""))[:19])}</td>'
        f'<td>{html.escape(str(r.get("event", "")))}</td>'
        f'<td class="dim">{html.escape(json.dumps({k: v for k, v in r.items() if k not in ("ts", "event")}))}</td></tr>'
        for r in state["audit"][-12:]
    )
    return f"<h2>gate audit (tail)</h2>{'<table>' + rows + '</table>' if rows else '<span class=dim>empty</span>'}"


def _sec_router(state: dict) -> str:
    ledger = []
    if LEDGER_PATH.exists():
        for line in LEDGER_PATH.read_text().splitlines()[-14:]:
            try:
                ledger.append(json.loads(line))
            except Exception:
                pass
    lrows = "".join(
        f'<tr><td class="dim">{time.strftime("%H:%M:%S", time.localtime(r.get("ts", 0)))}</td>'
        f'<td>{html.escape(str(r.get("event", "")))}</td>'
        f'<td>{html.escape(str(r.get("route", "")))}</td>'
        f'<td class="dim">{html.escape(str(r.get("engine", "")))}</td>'
        f'<td class="dim">${float(r.get("cost", 0)):.5f}</td></tr>'
        for r in reversed(ledger)
    )
    return f"""<h2>route tester</h2>
<input id=q placeholder='utterance…'>
<button id=route>Route&nbsp;<span class=dim>[enter]</span></button>
<div id=routestatus class=dim></div>
<div id=routeresult></div>
<h2>decision ledger</h2>{'<table><tr><th>ts</th><th>event</th><th>route</th><th>engine</th><th>cost</th></tr>' + lrows + '</table>' if lrows else '<span class=dim>empty</span>'}"""


def _sec_health(state: dict) -> str:
    api = state["api_url"] or "<unset>"
    up = state["health"] == 200
    return (
        f'<div class=sub>control plane at <a href="{api}">{api}</a>'
        f'&nbsp;<span class="dot {"up" if up else "down"}"></span>{"UP" if up else "DOWN"}</div>'
    )


def _rail(runs: list) -> str:
    if not runs:
        return '<div class=tl>no runs yet — n submits a goal</div>'
    out = []
    for r in runs[:8]:
        st = r["states"]
        active = st.get("LEASED", 0) + st.get("RUNNING", 0)
        failed = st.get("FAILED", 0)
        done = st.get("DONE", 0)
        total = r["subtasks"]
        word = (
            "FAILED" if failed else ("RUNNING" if active else ("DONE" if done == total else "QUEUED"))
        )
        goal = html.escape(r["goal"][:40]) if r["goal"] else r["run"][:12]
        out.append(
            f'<a class=runrow href="/tasks/{r["run"]}"><span class=g>{goal}</span>'
            f'<span class=s><span class="chip st-{word}">{word}</span> {done}/{total}</span></a>'
        )
    return "".join(out)


def _shell(title: str, active: str, body: str, rail: str = "") -> str:
    nav = "".join(
        f'<a href="{path}" class="{"on" if label == active else ""}"><span>{label}</span><span class=k>g {key}</span></a>'
        for path, label, key in VIEWS
    )
    pal = "".join(
        f'<a href="{path}" data-k="{key}">{label}</a>' for path, label, key in VIEWS
    )
    return f"""<!doctype html><meta charset=utf-8><title>open-computer · {title}</title>
<style>{CSS}</style>
<aside>
<div class=brand>open-computer</div>
<div class=sect>tasks</div>
{rail or '<div class=tl>no runs yet — n submits a goal</div>'}
<div class=sect>views</div>
<nav class=nav>{nav}</nav>
<div style="margin-top:auto" class=tl>press / for palette<br>press ? for keys</div>
</aside>
<main>{body}</main>
<div id=pal><div class=box><div class=h>views</div>{pal}</div></div>
<div id=help>g&lt;key&gt; views · / palette</div>
<script>
const pal=document.getElementById('pal');
let gArm=0;
addEventListener('keydown',e=>{{
  const tag=document.activeElement&&document.activeElement.tagName;
  if(e.key==='Escape'){{pal.classList.remove('on');return;}}
  if(tag==='INPUT')return;
  if(e.key==='/'||e.key==='?'){{pal.classList.toggle('on');e.preventDefault();return;}}
  if(e.key==='g'){{gArm=Date.now();return;}}
  if(gArm&&Date.now()-gArm<900){{
    const map={{w:'/',g:'/gate',m:'/matrix',f:'/fleet',r:'/router',o:'/ops'}};
    if(map[e.key]){{location.href=map[e.key];}}
    gArm=0;return;
  }}
}});
</script>"""


def _page_work(state: dict, runs: list) -> str:
    rows = "".join(
        f'<div class=subtask><div class=head><a href="/tasks/{r["run"]}">{html.escape(r["goal"] or r["run"])}</a>'
        f'<span class="chip st-{("FAILED" if r["states"].get("FAILED") else "RUNNING" if r["states"].get("LEASED") or r["states"].get("RUNNING") else "DONE" if r["states"].get("DONE") == r["subtasks"] else "QUEUED")}">'
        f'{"FAILED" if r["states"].get("FAILED") else "RUNNING" if r["states"].get("LEASED") or r["states"].get("RUNNING") else "DONE" if r["states"].get("DONE") == r["subtasks"] else "QUEUED"}</span></div>'
        f'<div class=meta>{r["subtasks"]} subtasks · {r["states"].get("DONE",0)} done'
        + (f' · <span class=fail>{r["states"]["FAILED"]} failed</span>' if r["states"].get("FAILED") else "")
        + f'</div></div>'
        for r in runs
    )
    body = (
        "<h1>work</h1><div class=sub>goal in — the orchestrator does the rest</div>"
        '<div class=goalbar><input id=goal placeholder="describe the outcome — e.g. compare two approaches and report" '
        'autocomplete=off><button id=go>submit</button></div>'
        '<div id=goalstat class=dim></div>'
        f"<h2>runs</h2>{rows or '<div class=dim>no runs yet</div>'}"
    )
    return _shell("work", "work", body + _SCRIPTS + _GOAL_JS, rail=_rail(runs))


def _page_run(state: dict, run: dict, runs: list) -> str:
    subs = []
    for s in run["subtasks"]:
        meta = (
            f'{s["engine"]} · {s["route"]} · {s["placement"]} — {html.escape(s["reason"] or "")}'
            + (f' · ${s["cost"]:.5f}' if s.get("cost") else "")
            + (f' · <span class=fail>gate</span>' if s.get("gate_required") else "")
            + (f' · pii: {",".join(s["pii"])}' if s.get("pii") else "")
        )
        result = (
            f'<div class=result>{html.escape(str(s["result"]))}</div>'
            if "result" in s
            else ""
        )
        subs.append(
            f'<div class=subtask><div class=head><span>{html.escape(s["subtask"] or "?")}</span>'
            f'<span class="chip st-{s["state"]}">{s["state"]}</span></div>'
            f'<div class=meta>{meta}</div>{result}</div>'
        )
    fails = "".join(
        f'<div class=meta><span class=fail>worker_fail</span> {html.escape(f.get("error",""))} — {f.get("task","")[:12]}</div>'
        for f in run["worker_failures"]
    )
    active = any(s["state"] not in ("DONE", "FAILED") for s in run["subtasks"]) or (
        len(run["subtasks"]) < 1
    )
    refresh = '<meta http-equiv="refresh" content="4">' if active else ""
    body = (
        f"{refresh}<h1>run {html.escape(run['run'])}</h1>"
        f'<div class=sub>{html.escape(run["goal"])}</div>'
        + "".join(subs)
        + (f"<h2>worker failures</h2>{fails}" if fails else "")
        + '<div class="dim" style="margin-top:14px"><a href="/">← work</a></div>'
    )
    return _shell("run", "work", body, rail=_rail(runs))


def _page_matrix(state: dict) -> str:
    return _shell("matrix", "matrix", "<h1>matrix</h1>" + _sec_health(state) + _sec_matrix(state) + _SCRIPTS, rail=_rail(state.get("runs", [])))


def _page_fleet(state: dict) -> str:
    return _shell("fleet", "fleet", "<h1>fleet</h1>" + _sec_health(state) + _sec_fleet(state), rail=_rail(state.get("runs", [])))


def _page_gate(state: dict) -> str:
    return _shell("gate", "gate", "<h1>gate</h1>" + _sec_health(state) + _sec_gate(state), rail=_rail(state.get("runs", [])))


def _page_router(state: dict) -> str:
    return _shell("router", "router", "<h1>router</h1>" + _sec_router(state) + _SCRIPTS, rail=_rail(state.get("runs", [])))


_SCRIPTS = """<script>
const run=async()=>{const b=document.getElementById('run');if(!b)return;b.disabled=1;
document.getElementById('status').textContent='running conformance (boots real microVMs, ~7s)…';
const r=await fetch('/run');const j=await r.json();
document.getElementById('matrix').innerHTML=j.matrix;document.getElementById('status').textContent=j.status;b.disabled=0;};
const rb=document.getElementById('run');if(rb)rb.onclick=run;
const routeIt=async()=>{const q=document.getElementById('q').value.trim();if(!q)return;
document.getElementById('routestatus').textContent='routing…';
const r=await fetch('/route?q='+encodeURIComponent(q));const j=await r.json();
document.getElementById('routestatus').textContent='';
document.getElementById('routeresult').innerHTML=j.html;};
const rt=document.getElementById('route');if(rt)rt.onclick=routeIt;
const qi=document.getElementById('q');if(qi)qi.addEventListener('keydown',e=>{if(e.key==='Enter')routeIt();});
addEventListener('keydown',e=>{if(e.key==='r'&&!e.metaKey&&!e.ctrlKey&&document.activeElement.tagName!=='INPUT'&&document.getElementById('run'))run();});
</script>"""


_GOAL_JS = """<script>
const go=async()=>{const g=document.getElementById('goal').value.trim();if(!g)return;
document.getElementById('goalstat').textContent='decomposing + dispatching…';
const r=await fetch('/api/goal',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({goal:g})});
if(!r.ok){const j=await r.json().catch(()=>({}));document.getElementById('goalstat').textContent='rejected — '+(j.error||r.status);return;}
const j=await r.json();location.href='/tasks/'+j.run;};
const gb=document.getElementById('go');if(gb)gb.onclick=go;
const gi=document.getElementById('goal');if(gi)gi.addEventListener('keydown',e=>{if(e.key==='Enter')go();});
addEventListener('keydown',e=>{if(e.key==='n'&&document.activeElement.tagName!=='INPUT'&&gi){gi.focus();e.preventDefault();}});
</script>"""


class Handler(BaseHTTPRequestHandler):
    state: dict = {}
    auth = Auth()

    def _rp(self) -> str:
        return rp_for(self.headers.get("Host", "localhost"))

    def _identity(self) -> str | None:
        raw = self.headers.get("Cookie", "")
        cookie = next(
            (p.split("=", 1)[1] for p in raw.split(";") if p.strip().startswith("oc_sess=")),
            None,
        )
        return self.auth.check_cookie(self._rp(), cookie)

    def _json_body(self) -> dict:
        try:
            return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        except Exception:
            return {}

    def _set_session(self, cookie: str):
        secure = "" if self._rp() == "localhost" else "; Secure"
        self.send_header(
            "Set-Cookie",
            f"oc_sess={cookie}; HttpOnly{secure}; SameSite=Strict; Path=/; Max-Age=43200",
        )

    def _state(self) -> dict:
        api = os.environ.get("CUBE_API_URL", "").rstrip("/")
        key = os.environ.get("CUBE_API_KEY", "")
        code, body = _get(f"{api}/health", key) if api else (0, "no api url")
        _, sbody = _get(f"{api}/sandboxes", key) if code == 200 else (0, "[]")
        try:
            sandboxes = json.loads(sbody)
        except Exception:
            sandboxes = []
        audit_file = Path(os.environ.get("OC_AUDIT", "/tmp/oc-audit/audit.ndjson"))
        audit = []
        if audit_file.exists():
            audit = [
                json.loads(l) for l in audit_file.read_text().splitlines() if l.strip()
            ]
        from services.opsview import runs as runs_mod

        return {
            "api_url": api,
            "health": code,
            "sandboxes": sandboxes,
            "audit": audit,
            "matrix": Handler.state.get("matrix", {}),
            "runs": runs_mod.list_runs(),
        }

    def do_GET(self):
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(self.path)
        path = parsed.path

        # --- public: auth surface ------------------------------------
        if path == "/login":
            self._send(200, login_page().encode())
            return
        if path == "/enroll":
            token = (parse_qs(parsed.query).get("token") or [""])[0]
            self._send(200, enroll_page(token).encode())
            return

        # --- gate -----------------------------------------------------
        if not self._identity():
            self.send_response(302)
            self.send_header("Location", "/login")
            self.end_headers()
            return

        if path == "/route":
            q = (parse_qs(parsed.query).get("q") or [""])[0]
            try:
                d = _route(q)
                row = (
                    f"<table><tr><td>route</td><td>{d['route']}</td></tr>"
                    f"<tr><td>tier</td><td>{d['tier']}</td></tr>"
                    f"<tr><td>engine</td><td class='dim'>{d['engine']}</td></tr>"
                    f"<tr><td>confidence</td><td>{d['confidence']}</td></tr>"
                    f"<tr><td>cost</td><td class='dim'>${d['cost']:.5f}</td></tr>"
                    f"<tr><td>ms</td><td class='dim'>{d['ms']}</td></tr></table>"
                )
            except Exception as e:
                row = f"<span class='fail'>{html.escape(str(e))}</span>"
            self._send(200, json.dumps({"html": row}).encode(), "application/json")
            return
        if path == "/run":
            out = _run_matrix()
            matrix = {m[0]: (m[1], m[2]) for m in MATRIX_RE.findall(out)}
            Handler.state["matrix"] = matrix
            tail = out.strip().splitlines()[-1] if out.strip() else ""
            body = json.dumps(
                {"matrix": _matrix_table(matrix), "status": html.escape(tail)}
            ).encode()
            self._send(200, body, "application/json")
            return

        state = self._state()
        if path == "/" or path == "/ops":
            self._send(200, _page_work(state, state["runs"]).encode())
            return
        if path.startswith("/tasks/"):
            rid = path.removeprefix("/tasks/").strip("/")
            from services.opsview import runs as runs_mod

            run = runs_mod.get_run(rid) if rid else None
            if run is None:
                self._send(404, b"run not found")
                return
            self._send(200, _page_run(state, run, state["runs"]).encode())
            return
        page = {
            "/matrix": _page_matrix,
            "/fleet": _page_fleet,
            "/gate": _page_gate,
            "/router": _page_router,
        }.get(path)
        if page is None:
            self._send(404, b"not found")
            return
        self._send(200, page(state).encode())

    def do_POST(self):
        from urllib.parse import urlparse

        path = urlparse(self.path).path

        if path == "/api/goal":
            if not self._identity():
                self._send(401, b'{"error":"unauthenticated"}', "application/json")
                return
            body = self._json_body()
            goal = (body.get("goal") or "").strip()
            if not goal or len(goal) > 4000:
                self._send(400, b'{"error":"goal required"}', "application/json")
                return
            try:
                from services.opsview import runs as runs_mod
                from services.orchestrator.engines import decompose_engine, work_fn

                rid = runs_mod.submit(goal, decompose_engine(), work_fn())
            except Exception as e:
                self._send(
                    502,
                    json.dumps({"error": f"{type(e).__name__}: {e}"}).encode(),
                    "application/json",
                )
                return
            self._send(200, json.dumps({"run": rid}).encode(), "application/json")
            return

        if not path.startswith("/webauthn/"):
            self._send(404, b'{"error":"not found"}', "application/json")
            return
        body = self._json_body()
        rp = self._rp()

        if path == "/webauthn/login/begin":
            opts, tok = self.auth.begin_auth(rp)
            self._send(200, json.dumps({"publicKey": opts["publicKey"], "tok": tok}).encode(), "application/json")
            return
        if path == "/webauthn/login/complete":
            try:
                got = self.auth.complete_auth(body.get("tok", ""), json.dumps(body.get("credential", {})))
            except Exception:
                got = None
            if not got:
                self._send(401, b'{"error":"denied"}', "application/json")
                return
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
                self._send(403, b'{"error":"invalid or expired enroll token"}', "application/json")
                return
            opts, tok = self.auth.begin_register(rp, "luke")
            self._send(200, json.dumps({"publicKey": opts["publicKey"], "tok": tok}).encode(), "application/json")
            return
        if path == "/webauthn/register/complete":
            try:
                cred_id = self.auth.complete_register(
                    body.get("tok", ""), json.dumps(body.get("credential", {}))
                )
            except Exception:
                cred_id = None
            if not cred_id:
                self._send(401, b'{"error":"enrollment failed"}', "application/json")
                return
            self._send(200, b'{"ok":true}', "application/json")
            return
        self._send(404, b'{"error":"not found"}', "application/json")

    def _send(self, code, body, ctype="text/html"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def main(port: int = 8791):
    print(f"ops view → http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8791)
