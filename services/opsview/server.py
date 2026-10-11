"""Local ops view — one dense page: matrix, sandboxes, gate audit.

stdlib only. Every number is live (probed on request or on matrix run);
nothing is stubbed. States render explicitly: UP/DOWN, PASS/FAIL/PENDING.

Run:  CUBE_API_URL=... CUBE_API_KEY=... python -m services.opsview.server
Open: http://127.0.0.1:8791   (r = run matrix, ↓/j scroll)
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

REPO = Path(__file__).resolve().parents[2]
MATRIX_RE = re.compile(r"^(R\d+): (PASS|FAIL|PENDING) \((\d+) checks\)", re.M)

CSS = """
:root{color-scheme:dark;--bg:#0b0e11;--fg:#d7dde3;--dim:#7d8a96;--ok:#3fb96f;
--bad:#e05d5d;--pend:#c9a227;--line:#1d242b;--acc:#4d9fff}
*{box-sizing:border-box;margin:0}
body{background:var(--bg);color:var(--fg);font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;
padding:20px 24px;max-width:1100px}
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
#status{white-space:pre;color:var(--dim);margin-top:8px}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px}
.up{background:var(--ok)}.down{background:var(--bad)}
a{color:var(--acc);text-decoration:none}
"""


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
                "uv",
                "run",
                "--with",
                "pytest",
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

    r = default_router("/tmp/oc-audit/router-ledger.jsonl")
    t0 = time.time()
    d = r.route(q)
    return {
        "route": d.route,
        "tier": d.tier,
        "engine": d.engine,
        "confidence": d.confidence,
        "cost": d.cost,
        "ms": round(d.ms * 1000) if d.ms < 10 else round(d.ms),
    }


def _page(state: dict) -> str:
    api = state["api_url"] or "<unset>"
    up = state["health"] == 200
    rows = []
    for rule, (st, n) in sorted(state["matrix"].items()):
        cls = st.lower()
        rows.append(f'<tr><td>{rule}</td><td class="{cls}">{st}</td><td class="dim">{n} checks</td></tr>')
    matrix_html = (
        "<table>" + "".join(rows) + "</table>"
        if rows
        else '<span class="dim">not run — press r or Run matrix</span>'
    )
    sb_rows = []
    for sb in state["sandboxes"]:
        sb_rows.append(
            f'<tr><td>{html.escape(str(sb.get("sandboxID", "?"))[:12])}</td>'
            f'<td>{html.escape(str(sb.get("templateID", "?"))[:16])}</td>'
            f'<td>{html.escape(str(sb.get("status", "?")))}</td></tr>'
        )
    sb_html = (
        "<table><tr><th>sandbox</th><th>template</th><th>status</th></tr>"
        + "".join(sb_rows)
        + "</table>"
        if sb_rows
        else '<span class="dim">none running</span>'
    )
    audit_rows = "".join(
        f'<tr><td class="dim">{html.escape(str(r.get("ts",""))[:19])}</td>'
        f'<td>{html.escape(str(r.get("event","")))}</td>'
        f'<td class="dim">{html.escape(json.dumps({k:v for k,v in r.items() if k not in ("ts","event")}))}</td></tr>'
        for r in state["audit"][-12:]
    )
    audit_html = (
        "<table>" + audit_rows + "</table>" if audit_rows else '<span class="dim">empty</span>'
    )
    dot = "up" if up else "down"
    return f"""<!doctype html><meta charset=utf-8><title>open-computer · ops</title>
<style>{CSS}</style>
<h1>open-computer · ops</h1>
<div class=sub>control plane at <a href="{api}">{api}</a>
&nbsp;<span class="dot {dot}"></span>{'UP' if up else 'DOWN'}</div>

<h2>determinism matrix</h2>
<button id=run>Run matrix&nbsp;<span class=dim>[r]</span></button>
<div id=status></div>
<div id=matrix>{matrix_html}</div>

<h2>route tester</h2>
<input id=q placeholder='utterance…' style="background:#141a20;border:1px solid var(--line);color:var(--fg);font:inherit;padding:4px 10px;width:340px">
<button id=route>Route&nbsp;<span class=dim>[enter]</span></button>
<div id=routestatus class=dim></div>
<div id=routeresult></div>

<h2>sandboxes ({len(state['sandboxes'])})</h2>{sb_html}

<h2>gate audit (tail)</h2>{audit_html}
<div class=sub style="margin-top:18px">refreshed {time.strftime('%H:%M:%S')}</div>
<script>
const run=async()=>{{const b=document.getElementById('run');b.disabled=1;
document.getElementById('status').textContent='running conformance (boots real microVMs, ~7s)…';
const r=await fetch('/run');const j=await r.json();
document.getElementById('matrix').innerHTML=j.matrix;document.getElementById('status').textContent=j.status;b.disabled=0;}};
document.getElementById('run').onclick=run;
const routeIt=async()=>{{const q=document.getElementById('q').value.trim();if(!q)return;
document.getElementById('routestatus').textContent='routing…';
const r=await fetch('/route?q='+encodeURIComponent(q));const j=await r.json();
document.getElementById('routestatus').textContent='';
document.getElementById('routeresult').innerHTML=j.html;}};
document.getElementById('route').onclick=routeIt;
document.getElementById('q').addEventListener('keydown',e=>{{if(e.key==='Enter')routeIt();}});
addEventListener('keydown',e=>{{if(e.key==='r'&&!e.metaKey&&!e.ctrlKey&&document.activeElement.id!=='q')run();}});
</script>"""


class Handler(BaseHTTPRequestHandler):
    state: dict = {}

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
        return {
            "api_url": api,
            "health": code,
            "sandboxes": sandboxes,
            "audit": audit,
            "matrix": Handler.state.get("matrix", {}),
        }

    def do_GET(self):
        if self.path.startswith("/route"):
            from urllib.parse import parse_qs, urlparse

            q = (parse_qs(urlparse(self.path).query).get("q") or [""])[0]
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
            body = json.dumps({"html": row}).encode()
            self._send(200, body, "application/json")
            return
        if self.path == "/run":
            out = _run_matrix()
            matrix = {m[0]: (m[1], m[2]) for m in MATRIX_RE.findall(out)}
            Handler.state["matrix"] = matrix
            rows = "".join(
                f'<tr><td>{r}</td><td class="{s.lower()}">{s}</td><td class="dim">{n} checks</td></tr>'
                for r, (s, n) in sorted(matrix.items())
            )
            tail = out.strip().splitlines()[-1] if out.strip() else ""
            body = json.dumps(
                {"matrix": f"<table>{rows}</table>", "status": html.escape(tail)}
            ).encode()
            self._send(200, body, "application/json")
            return
        self._send(200, _page(self._state()).encode())

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
