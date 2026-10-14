"""Server-rendered pages for the operator console (Silver Lining).

Pure functions from state to HTML. No colors here: every style is a class in
console.css that reads tokens from silver-lining.css. No inline styles or
scripts (the CSP forbids both), so everything behaves with the strict policy.
"""

from __future__ import annotations

import html
import json
import time
from datetime import datetime, timezone
from urllib.parse import quote, urlparse

from . import registry

e = html.escape

PAGE_TITLES = {
    "work": "Work", "overview": "Overview", "goals": "Goals", "automations": "Automations",
    "skills": "Skills", "sandboxes": "Sandboxes",
    "runs": "Runs", "approvals": "Approvals", "router": "Router", "audit": "Audit",
}
NAV_PAGE = {a.id.split(".", 1)[1]: a for a in registry.nav_actions()}

ASSET_V = "2"

# ---------------------------------------------------------------- formatting


def abs_time(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def rel_time(ts: float, now: float | None = None) -> str:
    """Relative within 7 days ("2h ago"), a date beyond that."""
    now = time.time() if now is None else now
    d = now - ts
    if d < -60:
        return "in the future"
    d = max(d, 0)
    if d < 10:
        return "just now"
    if d < 60:
        return f"{int(d)}s ago"
    if d < 3600:
        return f"{int(d // 60)}m ago"
    if d < 86400:
        return f"{int(d // 3600)}h ago"
    if d < 7 * 86400:
        return f"{int(d // 86400)}d ago"
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%b %-d, %Y")


def time_tag(ts, now: float | None = None) -> str:
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        return '<span class="muted">unknown</span>'
    iso = datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")
    return f'<time datetime="{iso}" title="{abs_time(ts)}">{rel_time(ts, now)}</time>'


def duration(s: float) -> str:
    s = int(round(s))
    m, sec = divmod(s, 60)
    return f"{m}m {sec}s" if m else f"{sec}s"


def money(x: float) -> str:
    x = float(x or 0)
    if x == 0:
        return "$0.00"
    if x >= 0.01:
        return f"${x:.2f}"
    s = f"{x:.6f}".rstrip("0")
    return "$" + s + "0" * max(0, 4 - len(s.split(".")[1]))


def host_of(url: str) -> str:
    return urlparse(url).netloc or url


# ------------------------------------------------------------------- atoms

ICONS = {
    "ok": '<path d="M3 8.5l3.2 3L13 4.5"/>',
    "urgent": '<path d="M4 4l8 8M12 4l-8 8"/>',
    "attention": '<path d="M8 2.5l6 10.5H2z"/><path d="M8 6.5v3M8 11.2v.1"/>',
    "neutral": '<circle cx="8" cy="8" r="4.5"/>',
    "back": '<path d="M6 4L2.5 7.5 6 11M3 7.5h6.5a3.5 3.5 0 010 7H8"/>',
    "menu": '<path d="M2.5 4.5h11M2.5 8h11M2.5 11.5h11"/>',
    "search": '<circle cx="7" cy="7" r="4"/><path d="M10 10l3.5 3.5"/>',
    "theme": '<circle cx="8" cy="8" r="3"/><path d="M8 1.5v1.5M8 13v1.5M1.5 8H3M13 8h1.5M3.4 3.4l1 1M11.6 11.6l1 1M12.6 3.4l-1 1M4.4 11.6l-1 1"/>',
    "external": '<path d="M9 3h4v4M13 3L7.5 8.5M11 9v3.5H3.5V5H7"/>',
}


def sprite() -> str:
    syms = "".join(f'<symbol id="i-{k}" viewBox="0 0 16 16">{v}</symbol>' for k, v in ICONS.items())
    glint = '<symbol id="i-glint" viewBox="0 0 16 16"><path d="M8 1l1.7 5.3L15 8l-5.3 1.7L8 15l-1.7-5.3L1 8l5.3-1.7z" class="fill"/></symbol>'
    return f'<svg width="0" height="0" class="sprite" aria-hidden="true" focusable="false">{syms}{glint}</svg>'


def icon(name: str) -> str:
    return f'<svg class="ic" aria-hidden="true" focusable="false"><use href="#i-{name}"/></svg>'


# state word -> (token class, icon). Always glyph + word, never color alone.
STATES = {
    "passed": ("healthy", "ok"), "pass": ("healthy", "ok"), "up": ("healthy", "ok"),
    "approved": ("healthy", "ok"), "allowed": ("healthy", "ok"), "running": ("healthy", "ok"),
    "failed": ("urgent", "urgent"), "fail": ("urgent", "urgent"), "down": ("urgent", "urgent"),
    "blocked": ("urgent", "urgent"), "high": ("urgent", "urgent"),
    "pending": ("attention", "attention"), "waiting": ("attention", "attention"),
    "elevated": ("attention", "attention"),
    "sent back": ("neutral", "back"), "sent_back": ("neutral", "back"),
    "ready": ("healthy", "ok"), "active": ("healthy", "ok"),
    "paused": ("neutral", "neutral"),
    "not configured": ("neutral", "neutral"), "low": ("neutral", "neutral"),
    "unknown": ("neutral", "neutral"),
    "queued": ("neutral", "neutral"), "leased": ("attention", "attention"),
    "done": ("healthy", "ok"), "awaiting-approval": ("attention", "attention"),
}


def status(word: str, label: str | None = None) -> str:
    cls, ic = STATES.get(word.lower(), ("neutral", "neutral"))
    return f'<span class="st st-{cls}">{icon(ic)}<span>{e(label or word)}</span></span>'


def glint(who: str, session: str, ts) -> str:
    """Agent provenance glint (foundation section 10): the only use of the mark."""
    tip = f"Agent {who}" + (f", session {session}" if session else "") + f", {abs_time(ts)}"
    href = f"/audit?q={quote(session or who)}"
    return (f'<a class="glint" href="{e(href)}" title="{e(tip)}" '
            f'aria-label="{e(tip)}. Open its audit trail"><svg class="ic" aria-hidden="true" '
            f'focusable="false"><use href="#i-glint"/></svg></a>')


def empty(title: str, body: str, action: str = "") -> str:
    return f'<div class="empty"><p class="empty-title">{e(title)}</p><p class="muted">{body}</p>{action}</div>'


def skeleton_rows(n: int = 5, cols: int = 4) -> str:
    row = "".join('<span class="sk-cell"></span>' for _ in range(cols))
    return ('<div class="skeleton" aria-hidden="true">'
            + "".join(f'<div class="sk-row">{row}</div>' for _ in range(n)) + "</div>")


# ------------------------------------------------------------------- shell


def shell(page: str, body: str, *, csrf: str, pending: int = 0, who: str = "",
          conn: str = "", sub: str = "") -> str:
    nav = []
    for key in registry.PAGES:
        a = NAV_PAGE[key]
        cur = ' aria-current="page"' if key == page else ""
        badge = (f'<span class="badge" aria-label="{pending} waiting">{pending}</span>'
                 if key == "approvals" and pending else "")
        nav.append(
            f'<li><a href="{a.href}" data-action="{a.id}"{cur}><span>{e(a.label)}</span>'
            f'{badge}<kbd>{e(a.keys)}</kbd></a></li>'
        )
    bottom = "".join(
        f'<a href="{NAV_PAGE[k].href}"{" aria-current=page" if k == page else ""}>{e(NAV_PAGE[k].label)}</a>'
        for k in ("work", "overview", "approvals", "runs")
    )
    title = PAGE_TITLES[page]
    return f"""<!doctype html>
<html lang="en" data-page="{page}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="csrf-token" content="{e(csrf)}">
<title>{title} · open-computer</title>
<link rel="stylesheet" href="/static/silver-lining.css?v={ASSET_V}">
<link rel="stylesheet" href="/static/console.css?v={ASSET_V}">
<script src="/static/theme-init.js?v={ASSET_V}"></script>
</head>
<body>
{sprite()}
<a class="skip" href="#main">Skip to content</a>
<div class="app">
<aside class="side" id="sidebar" aria-label="Console">
  <div class="brand">open-computer</div>
  <nav aria-label="Sections"><ul class="nav">{"".join(nav)}</ul></nav>
  <div class="side-foot">
    <button type="button" class="btn ghost" data-action="theme">{icon("theme")}<span>Theme</span></button>
    <button type="button" class="btn ghost" data-action="help"><span>Shortcuts</span><kbd>?</kbd></button>
    <p class="muted caption">{e(who)}</p>
  </div>
</aside>
<div class="col">
<header class="head">
  <button type="button" class="btn ghost menu" id="menu" aria-expanded="false" aria-controls="sidebar" aria-label="Open navigation">{icon("menu")}</button>
  <h1>{title}</h1>
  <span class="head-sub muted">{sub}</span>
  <span class="grow"></span>
  {conn}
  <button type="button" class="btn palette-btn" data-action="palette" aria-label="Search or run a command ctrl+k">{icon("search")}<span class="hide-sm">Search or run a command</span> <kbd>ctrl+k</kbd></button>
</header>
<main id="main" tabindex="-1">
{body}
</main>
</div>
</div>
<nav class="bottombar" aria-label="Quick actions">{bottom}</nav>
<div id="layers"></div>
<div id="toasts" class="toasts" role="status" aria-live="polite"></div>
<div id="alerts" class="toasts" role="alert" aria-live="assertive"></div>
<script type="application/json" id="oc-actions">{registry.payload(page)}</script>
<script src="/static/console.js?v={ASSET_V}" defer></script>
{f'<script src="/static/work.js?v={ASSET_V}" defer></script>' if page == "work" else ""}
</body>
</html>"""


def conn_pill(fleet: dict) -> str:
    if not fleet["configured"]:
        return f'<span class="pill">{status("not configured", "Control plane not configured")}</span>'
    word = "up" if fleet["up"] else "down"
    return f'<span class="pill">{status(word, "Control plane " + word.upper())}</span>'


# ------------------------------------------------------------------ fleet


def fleet_rows(fleet: dict) -> str:
    if not fleet["configured"]:
        return empty("Control plane not configured",
                     "Set <code>CUBE_API_URL</code> and <code>CUBE_API_KEY</code> to list sandboxes. "
                     "Nothing here is guessed.")
    if not fleet["up"]:
        return empty("Control plane is down",
                     f"{e(fleet['api_host'])} did not answer. The sandbox inventory is unknown, not empty. "
                     "Check that CubeSandbox is running, then reload.")
    sb = fleet["sandboxes"]
    if not sb:
        return empty("No sandboxes running",
                     "A sandbox appears here while a run holds it. Start one with a matrix run.",
                     '<a class="btn primary" href="/runs/new">New matrix run</a>')
    rows = "".join(
        f'<tr data-row data-href="/sandboxes/{quote(str(s.get("sandboxID", "?")), safe="")}">'
        f'<td class="mono"><a href="/sandboxes/{quote(str(s.get("sandboxID", "?")), safe="")}">{e(str(s.get("sandboxID", "?")))}</a></td>'
        f'<td class="mono">{e(str(s.get("templateID", "?")))}</td>'
        f'<td>{status(str(s.get("status", "unknown")))}</td></tr>'
        for s in sb
    )
    return (f'<table class="dense" data-table><caption class="sr">Sandboxes</caption>'
            f'<thead><tr><th scope="col">Sandbox</th><th scope="col">Template</th>'
            f'<th scope="col">State</th></tr></thead><tbody>{rows}</tbody></table>')


def page_sandboxes(fleet: dict, inline: bool) -> str:
    if inline:
        inner = fleet_rows(fleet)
        attrs = ""
    else:
        inner = skeleton_rows(5, 3)
        attrs = ' data-frag="/_frag/fleet" aria-busy="true"'
    dest = (f'Inventory read from <span class="mono">{e(fleet["api_host"])}</span>. Read only.'
            if fleet["configured"] else "No control plane configured.")
    return f'<p class="muted note">{dest}</p><section aria-label="Sandboxes"{attrs}>{inner}</section>'


def page_sandbox_detail(sid: str, row: dict | None) -> str:
    if row is None:
        return empty("Sandbox not found", f"{e(sid)} is not in the current inventory. It may have been reclaimed.",
                     '<a class="btn" href="/sandboxes">Back to sandboxes</a>')
    rows = "".join(
        f'<tr><th scope="row">{e(str(k))}</th><td class="mono">{e(json.dumps(v) if not isinstance(v, str) else v)}</td></tr>'
        for k, v in sorted(row.items())
    )
    return f'<table class="kv dense"><caption class="sr">Sandbox {e(sid)}</caption><tbody>{rows}</tbody></table>'


# --------------------------------------------------------------- approvals


def risk_of(item: dict) -> tuple[str, str]:
    if item.get("pii"):
        return "high", "PII: " + ", ".join(item["pii"])
    if float(item.get("est_cost") or 0) >= 0.05:
        return "elevated", "High spend"
    return "low", "No PII, within budget"


def approval_row(item: dict, now: float) -> str:
    risk, why = risk_of(item)
    cost = money(item.get("est_cost", 0))
    payload = json.dumps(item.get("payload", {}), indent=2, sort_keys=True)
    iid = e(item["id"])
    return f"""<tbody class="item" data-row data-approval="{iid}" data-hash="{e(item['payload_hash'])}" id="{iid}">
<tr class="item-main">
<td class="who">{glint(item.get("who", "agent"), item.get("session", ""), item["ts"])}<span>{e(item.get("who", "agent"))}</span></td>
<td class="what" title="{e(item.get("what", ""))}">{e(item.get("what", ""))}</td>
<td class="dest mono" title="Leaves this box for {e(item['egress'])}">{icon("external")}{e(item["egress"])}</td>
<td class="num">{cost}</td>
<td>{status(risk, f"{risk.title()} risk")}<span class="sr"> {e(why)}</span></td>
<td class="num">{time_tag(item["ts"], now)}</td>
<td class="acts">
<button type="button" class="btn primary" data-act="approve" aria-keyshortcuts="a">Approve <span class="cost">{cost}</span></button>
<button type="button" class="btn" data-act="sendback" aria-keyshortcuts="r">Send back</button></td>
</tr>
<tr class="item-detail" hidden><td colspan="7">
<p class="caption muted">{e(why)}. This exact payload (sha256 <span class="mono">{e(item["payload_hash"][:12])}</span>, {item["payload_bytes"]} bytes) goes to <span class="mono">{e(item["egress"])}</span> if you approve. Model {e(str(item["model"]))}.</p>
<pre class="payload mono" tabindex="0" aria-label="Payload for {iid}">{e(payload)}</pre>
</td></tr>
<tr class="item-note" hidden><td colspan="7">
<label for="note-{iid}" class="caption">Note to the agent (required)</label>
<textarea id="note-{iid}" rows="2" class="note-input" aria-describedby="note-err-{iid}"></textarea>
<p class="err caption" id="note-err-{iid}" role="alert"></p>
<button type="button" class="btn primary" data-act="sendback-submit">Send back to agent</button>
<button type="button" class="btn ghost" data-act="sendback-cancel">Cancel</button>
<span class="muted caption">Nothing leaves the box. ctrl+enter sends.</span>
</td></tr>
</tbody>"""


def page_approvals(items: list[dict], now: float) -> str:
    pend = [i for i in items if i["status"] == "pending"]
    done = [i for i in items if i["status"] != "pending"][::-1][:10]
    if pend:
        head = ('<thead><tr><th scope="col">Who</th><th scope="col">What</th><th scope="col">Goes to</th>'
                '<th scope="col" class="num">Cost</th><th scope="col">Risk</th><th scope="col" class="num">Asked</th>'
                '<th scope="col"><span class="sr">Actions</span></th></tr></thead>')
        table = ('<table class="dense approvals" data-table><caption class="sr">Pending approvals, oldest first</caption>'
                 + head + "".join(approval_row(i, now) for i in pend) + "</table>")
        lead = (f'<p class="muted note">{len(pend)} waiting, oldest first. '
                '<kbd>j</kbd>/<kbd>k</kbd> move, <kbd>enter</kbd> shows the payload, '
                '<kbd>a</kbd> approves, <kbd>r</kbd> sends back with a note.</p>')
    else:
        table, lead = "", empty(
            "Nothing waiting on you",
            "When an agent needs to send a payload off this box, the request lands here with who asked, "
            "what leaves, where it goes and what it costs.")
    drows = "".join(
        f'<tr><td>{status(i["status"].replace("_", " "))}</td><td>{e(i.get("what", ""))}</td>'
        f'<td class="mono">{e(i["egress"])}</td><td>{e(i.get("decided_by") or "")}</td>'
        f'<td class="muted">{e(i.get("note", ""))}</td><td class="num">{time_tag(i.get("decided_ts", i["ts"]), now)}</td></tr>'
        for i in done
    )
    decided = (f'<h2>Decided</h2><table class="dense"><caption class="sr">Recent decisions</caption><thead><tr>'
               '<th scope="col">Result</th><th scope="col">What</th><th scope="col">Went to</th>'
               '<th scope="col">By</th><th scope="col">Note</th><th scope="col" class="num">When</th></tr></thead>'
               f'<tbody>{drows}</tbody></table>') if done else ""
    return f'{lead}<div id="queue">{table}</div>{decided}'


# -------------------------------------------------------------------- runs


def rules_summary(rules: dict) -> str:
    c = {"PASS": 0, "FAIL": 0, "PENDING": 0}
    for st, _ in rules.values():
        c[st] = c.get(st, 0) + 1
    return " · ".join(f"{n} {k.lower()}" for k, n in c.items() if n) or "no rules parsed"


def page_runs(runs: list[dict], now: float) -> str:
    if not runs:
        return empty("No runs yet",
                     "A matrix run executes the determinism conformance checks and keeps its log here.",
                     '<a class="btn primary" href="/runs/new">New matrix run</a>')
    rows = "".join(
        f'<tr data-row data-href="/runs/{r["id"]}"><td>{status(r["status"])}</td>'
        f'<td class="mono"><a href="/runs/{r["id"]}">{r["id"]}</a></td><td>{e(r.get("by", ""))}</td>'
        f'<td>{e(rules_summary({k: tuple(v) for k, v in r.get("rules", {}).items()}))}</td>'
        f'<td class="num">{duration(r["duration_s"])}</td><td class="num">{r["lines"]}</td>'
        f'<td class="num">{time_tag(r["ts"], now)}</td></tr>'
        for r in runs
    )
    return ('<table class="dense" data-table><caption class="sr">Matrix runs, newest first</caption><thead><tr>'
            '<th scope="col">Result</th><th scope="col">Run</th><th scope="col">Started by</th>'
            '<th scope="col">Rules</th><th scope="col" class="num">Took</th><th scope="col" class="num">Log lines</th>'
            f'<th scope="col" class="num">Started</th></tr></thead><tbody>{rows}</tbody></table>')


def page_run_confirm(csrf: str, fleet: dict) -> str:
    dest = (f'<span class="mono">{e(fleet["api_host"])}</span>' if fleet["configured"]
            else "no control plane configured, so rules R1 to R3 will report PENDING")
    return f"""<section class="confirm" aria-labelledby="ct">
<h2 id="ct">Run the determinism matrix?</h2>
<table class="kv dense"><tbody>
<tr><th scope="row">Touches</th><td>CubeSandbox at {dest}</td></tr>
<tr><th scope="row">What happens</th><td>Boots real microVMs, runs the conformance checks, then reclaims them. About 7s.</td></tr>
<tr><th scope="row">Model spend</th><td>{money(0)}. No model is called.</td></tr>
<tr><th scope="row">Leaves this box</th><td>Nothing, apart from calls to the control plane above.</td></tr>
<tr><th scope="row">Command</th><td class="mono">pytest tests/conformance -q</td></tr>
</tbody></table>
<form method="post" action="/runs" class="actions" data-busy-form>
<input type="hidden" name="csrf" value="{e(csrf)}">
<input type="hidden" name="confirm" value="1">
<button type="submit" class="btn primary" id="go">Run matrix <span class="cost">{money(0)}</span></button>
<a class="btn" href="/runs">Cancel</a>
</form>
<div class="busy" hidden data-busy>{skeleton_rows(6, 1)}<p class="muted caption">Running conformance. This page updates when the run finishes.</p></div>
</section>"""


def log_viewer(text: str) -> str:
    lines = text.splitlines()
    data = json.dumps(lines).replace("</", "<\\/")
    tail = "\n".join(lines[-200:])
    return f"""<section aria-label="Run log" class="log-wrap">
<div class="log-meta caption muted"><span id="log-count">{len(lines)} lines</span> · line numbers on, showing a window of at most 5,000 lines in the page</div>
<div class="log" id="log" tabindex="0" role="log" aria-label="Run log, {len(lines)} lines. Scroll with the arrow keys, Page Up and Page Down." data-lines="{len(lines)}">
<noscript><pre class="mono">{e(tail)}</pre></noscript>
</div>
<script type="application/json" id="log-data">{data}</script>
</section>"""


def page_run_detail(row: dict, text: str, now: float) -> str:
    rules = "".join(
        f'<tr><td class="mono">{e(k)}</td><td>{status(v[0])}</td><td class="num">{v[1]} checks</td></tr>'
        for k, v in sorted(row.get("rules", {}).items())
    )
    rt = (f'<table class="dense kv"><caption class="sr">Rules in this run</caption><thead><tr><th scope="col">Rule</th>'
          f'<th scope="col">State</th><th scope="col" class="num">Checks</th></tr></thead><tbody>{rules}</tbody></table>'
          if rules else "")
    return (f'<p class="muted note">{status(row["status"])} · started {time_tag(row["ts"], now)} by {e(row.get("by", ""))} · '
            f'took {duration(row["duration_s"])}</p>{rt}<h2>Log</h2>{log_viewer(text)}')


# ------------------------------------------------------------------ router


def router_dest(paid: bool, base: str) -> str:
    if paid:
        return (f'Decider engine sends the utterance to <span class="mono">{e(host_of(base))}</span> '
                'and costs about $0.000004 per route. The table engine stays on this box.')
    return "No cloud key configured. Routes resolve in the local table engine: $0.00, nothing leaves this box."


def page_router(csrf: str, ledger: list[dict], spend: float, cap: float, paid: bool, base: str,
                now: float, result: str = "") -> str:
    pct = min(spend / cap, 1) if cap else 0
    cost = "~$0.000004" if paid else "$0.00"
    rows = "".join(
        f'<tr><td class="num">{time_tag(r.get("ts", 0), now)}</td><td>{e(str(r.get("event", "")))}</td>'
        f'<td>{e(str(r.get("route", "")))}</td><td class="mono">{e(str(r.get("engine", "")))}</td>'
        f'<td class="num">{money(r.get("cost", 0))}</td></tr>'
        for r in reversed(ledger)
    )
    ledger_t = (f'<table class="dense"><caption class="sr">Decision ledger, newest first</caption><thead><tr><th scope="col" class="num">When</th>'
                '<th scope="col">Event</th><th scope="col">Route</th><th scope="col">Engine</th><th scope="col" class="num">Cost</th></tr></thead>'
                f'<tbody>{rows}</tbody></table>') if rows else empty(
        "No decisions yet", "Every route decision is recorded here with the engine that made it and what it cost.")
    return f"""<section aria-labelledby="rt"><h2 id="rt">Route tester</h2>
<p class="muted note">{router_dest(paid, base)}</p>
<form method="post" action="/router/test" class="inline-form">
<input type="hidden" name="csrf" value="{e(csrf)}">
<label for="q" class="sr">Utterance to route</label>
<input id="q" name="q" type="text" class="input" placeholder="Describe a task to route" autocomplete="off" required aria-keyshortcuts="/">
<button type="submit" class="btn primary">Route <span class="cost">{cost}</span></button>
</form>
{result}
<div class="meter"><label for="spend" class="caption muted">Spent today {money(spend)} of {money(cap)} daily cap</label>
<progress id="spend" value="{pct:.4f}" max="1">{pct:.0%}</progress></div></section>
<h2>Decision ledger</h2>{ledger_t}"""


def router_confirm(csrf: str, q: str, base: str) -> str:
    return f"""<section class="confirm" aria-labelledby="ct"><h2 id="ct">Send this to the decider?</h2>
<table class="kv dense"><tbody>
<tr><th scope="row">Leaves this box</th><td>The utterance below, sent to <span class="mono">{e(host_of(base))}</span></td></tr>
<tr><th scope="row">Cost</th><td>About $0.000004, charged to the configured key</td></tr>
<tr><th scope="row">Utterance</th><td class="mono">{e(q)}</td></tr></tbody></table>
<form method="post" action="/router/test" class="actions">
<input type="hidden" name="csrf" value="{e(csrf)}"><input type="hidden" name="confirm" value="1">
<input type="hidden" name="q" value="{e(q)}">
<button type="submit" class="btn primary" id="go">Route it <span class="cost">~$0.000004</span></button>
<a class="btn" href="/router">Cancel</a></form></section>"""


def route_result(d: dict) -> str:
    rows = "".join(f"<tr><th scope=\"row\">{k}</th><td>{v}</td></tr>" for k, v in [
        ("Route", e(str(d["route"]))), ("Tier", e(str(d["tier"]))),
        ("Engine", f'<span class="mono">{e(str(d["engine"]))}</span>'),
        ("Confidence", e(str(d["confidence"]))), ("Cost", money(d["cost"])), ("Took", f'{d["ms"]} ms'),
    ])
    return f'<table class="kv dense result" aria-label="Route result"><tbody>{rows}</tbody></table>'


# ------------------------------------------------------------------- goals


def _fail_item(f: dict) -> str:
    """One worker_fail audit row as an inspector item: kind, detail, hint."""
    kind = f.get("kind")
    kind_t = f'<span class="chip err-chip mono">{e(str(kind))}</span> ' if kind else ""
    hint = f.get("hint")
    hint_t = f'<p class="muted caption w-hint">{e(str(hint))}</p>' if hint else ""
    return (f'<li><span class="mono">{e(str(f.get("task", "")))}</span> {kind_t}'
            f'{e(str(f.get("error", "")))}{hint_t}</li>')


def _state_chips(states: dict) -> str:
    chips = " ".join(
        f'{status(s)}<span class="muted caption">&nbsp;×{n}</span>' for s, n in states.items() if n
    )
    return chips or '<span class="muted">—</span>'


def page_goals(goals: list[dict], now: float) -> str:
    rows = "".join(
        f'<tr><td class="mono"><a href="/goals/{e(g["run"])}">{e(g["run"])}</a></td>'
        f'<td class="goal">{e(g["goal"])}</td><td>{_state_chips(g["states"])}</td>'
        f'<td class="num">{time_tag(g["ts"], now)}</td></tr>'
        for g in goals
    )
    table = (f'<table class="dense"><caption class="sr">Goal runs, newest first</caption><thead>'
             '<tr><th scope="col">Run</th><th scope="col">Goal</th><th scope="col">Subtasks</th>'
             '<th scope="col" class="num">Started</th></tr></thead>'
             f'<tbody>{rows}</tbody></table>') if rows else empty(
        "No goals yet",
        "Submit a goal and it decomposes into subtasks — each routed, placed, gated and worked.",
        action='<a class="btn primary" href="/goals/new">New goal</a>')
    return f"<section>{table}</section>"


def page_goal_new(csrf: str, error: str = "") -> str:
    err = f'<p class="err" role="alert">{e(error)}</p>' if error else ""
    return f"""<section aria-labelledby="gf"><h2 id="gf">New goal</h2>
<p class="muted note">One sentence. It decomposes into a subtask graph — each subtask is routed,
placed on this box or cloud, gated, then worked. Nothing runs until you confirm on the next step.</p>
{err}
<form method="post" action="/goals" class="goal-form">
<input type="hidden" name="csrf" value="{e(csrf)}">
<label for="goal" class="sr">Goal</label>
<textarea id="goal" name="goal" class="input" rows="3" maxlength="4000" required aria-keyshortcuts="n"
  placeholder="e.g. Compare three local embedding models and write up the winner"></textarea>
<button type="submit" class="btn primary">Decompose and review</button>
</form></section>"""


def page_goal_confirm(csrf: str, goal: str, cap: float) -> str:
    return f"""<section class="confirm" aria-labelledby="ct"><h2 id="ct">Run this goal?</h2>
<table class="kv dense"><tbody>
<tr><th scope="row">What happens</th><td>A planner call decomposes the goal into subtasks; each is routed,
placed, gated, then worked by the engine its placement picks. States update on the run page.</td></tr>
<tr><th scope="row">Leaves this box</th><td>Subtasks placed <span class="mono">cloud-*</span> go to
<span class="mono">openrouter.ai</span>. Anything the scanner flags as private stays local or waits
for approval — it never leaves unapproved.</td></tr>
<tr><th scope="row">Model spend</th><td>Planner plus one worker call per subtask, metered against the
{money(cap)} daily ledger cap.</td></tr>
<tr><th scope="row">Goal</th><td class="goal">{e(goal)}</td></tr>
</tbody></table>
<form method="post" action="/goals" class="actions" data-busy-form>
<input type="hidden" name="csrf" value="{e(csrf)}">
<input type="hidden" name="confirm" value="1">
<input type="hidden" name="goal" value="{e(goal)}">
<button type="submit" class="btn primary" id="go">Run it</button>
<a class="btn" href="/goals">Cancel</a>
</form>
<div class="busy" hidden data-busy>{skeleton_rows(4, 1)}<p class="muted caption">Decomposing and
dispatching. The run page fills in as subtasks settle.</p></div>
</section>"""


def page_goal_detail(run: dict, now: float) -> str:
    subs = run["subtasks"]
    active = (any(s["state"] not in ("DONE", "FAILED") for s in subs)
              or len(subs) < run["declared"])
    items = []
    for s in subs:
        meta = []
        if s.get("route"):
            meta.append(f'route {e(str(s["route"]))}')
        if s.get("engine"):
            meta.append(f'engine {e(str(s["engine"]))}')
        if s.get("placement"):
            meta.append(f'→ {e(str(s["placement"]))}')
        if s.get("reason"):
            meta.append(f'({e(str(s["reason"]))})')
        if s.get("gate_required"):
            meta.append("gate: approval required")
        if s.get("pii"):
            meta.append("pii: " + e(", ".join(map(str, s["pii"]))))
        if s.get("cost") is not None:
            meta.append(money(s["cost"]))
        res = (f'<pre class="mono result">{e(str(s["result"]))}</pre>'
               if s.get("result") else "")
        title = s.get("title") or str(s.get("subtask") or "")
        items.append(
            f'<li class="task"><header>{status(s["state"])} <span class="task-title">{e(title)}</span></header>'
            f'<p class="muted caption">{" · ".join(meta)}</p>{res}</li>')
    fails = "".join(_fail_item(f) for f in run["worker_failures"])
    fails_t = (f'<h2>Worker failures</h2><ul class="task-list">{fails}</ul>' if fails else "")
    refresh = ' data-refresh="3"' if active else ""
    pending_note = (f'<p class="muted caption">{run["declared"] - len(subs)} subtask(s) still queued '
                    'behind dependencies.</p>' if len(subs) < run["declared"] else "")
    head = (f'<p class="muted note">{e(run["goal"])}'
            + (f' · started {time_tag(run["started"], now)}' if run.get("started") else "")
            + '</p>')
    run_err = ""
    if run.get("error"):
        kind = f'<span class="chip err-chip mono">{e(str(run["error_kind"]))}</span> ' if run.get("error_kind") else ""
        hint = f'<p class="muted caption w-hint">{e(str(run["hint"]))}</p>' if run.get("hint") else ""
        run_err = f'<div class="w-err" role="alert">{kind}{e(str(run["error"]))}{hint}</div>'
    return (f'<section{refresh}>{head}{run_err}<ul class="task-list">{"".join(items)}</ul>'
            f'{pending_note}{fails_t}</section>')


# -------------------------------------------------------------------- work
# The workplace surface (/work): three panes, all server-rendered and swapped
# live by work.js from /_frag/work. Selection rides in location.hash so a
# refresh restores it. Approvals reuse approval_row — console.js's delegated
# approve/send-back handlers work unmodified inside the inspector.


def page_work(csrf: str) -> str:
    """Static skeleton; the panes fill in from the fragment endpoint. The
    composer is a real form — without JS it posts to /goals like /goals/new."""
    return f"""<div class="work" id="work">
<aside class="w-rail" aria-label="Goal composer and runs">
<form method="post" action="/goals" class="composer" id="composer">
<input type="hidden" name="csrf" value="{e(csrf)}">
<label for="wg" class="sr">New goal</label>
<textarea id="wg" name="goal" class="input" rows="3" maxlength="4000" required aria-keyshortcuts="/"
  placeholder="Describe a goal — it decomposes, routes, places and runs"></textarea>
<button type="submit" class="btn primary">Run</button>
</form>
<div id="w-rail" aria-live="polite">{skeleton_rows(6, 1)}</div>
</aside>
<section class="w-main" id="w-main" aria-label="Plan and results">{skeleton_rows(8, 1)}</section>
<aside class="w-inspect" id="w-inspect" aria-label="Inspector">{skeleton_rows(5, 1)}</aside>
</div>"""


def _dur(start: float | None, end: float | None) -> str:
    """Short duration: 0.4s / 12s / 3m."""
    if start is None or end is None:
        return ""
    d = max(0.0, end - start)
    if d < 10:
        return f"{d:.1f}s"
    if d < 120:
        return f"{d:.0f}s"
    return f"{d / 60:.0f}m"


def _rail_state(states: dict) -> str:
    """One aggregate word per run for the rail dot."""
    if states.get("FAILED"):
        return "failed"
    if states.get("LEASED") or states.get("RUNNING"):
        return "running"
    if states.get("AWAITING-APPROVAL"):
        return "waiting"
    total = sum(states.values())
    if total and states.get("DONE", 0) + states.get("FAILED", 0) == total:
        return "done"
    return "queued"


_STEP = {  # task state -> (css, icon, live verb)
    "DONE": ("done", "ok", "Done"),
    "FAILED": ("failed", "urgent", "Failed"),
    "LEASED": ("running", "neutral", "Working"),
    "RUNNING": ("running", "neutral", "Working"),
    "AWAITING-APPROVAL": ("waiting", "attention", "Awaiting approval"),
    "QUEUED": ("queued", "neutral", "Queued"),
}


def work_rail(runs: list[dict], now: float, sel: str) -> str:
    if not runs:
        return ('<h2>Runs</h2><p class="muted caption">No goal runs yet. '
                'Describe a goal above and it becomes one.</p>')
    items = []
    for r in runs:
        cur = ' cur" aria-current="true' if r["run"] == sel else ""
        st = _rail_state(r["states"])
        done = r["states"].get("DONE", 0)
        total = r["subtasks"] or sum(v for k, v in r["states"].items() if k != "QUEUED")
        meta = " · ".join(m for m in (
            rel_time(r["ts"], now) if r.get("ts") else "",
            f"{done}/{total}" if total else ("failed" if st == "failed" else ""),
        ) if m)
        items.append(
            f'<li><button type="button" class="w-run{cur}" data-run="{e(r["run"])}">'
            f'<span class="w-run-top"><span class="w-dot w-dot-{st}"></span>'
            f'<span class="w-run-goal">{e(r["goal"] or r["run"])}</span></span>'
            f'<span class="w-run-meta muted caption">{e(meta)}</span></button></li>')
    return f'<h2>Runs</h2><ul class="w-runs">{"".join(items)}</ul>'


def _thought_line(s: dict, now: float) -> str:
    """The honest narrative line under a step — what it was routed to and
    why, plus how long it took. Words from the audit row, never inferred."""
    st = s.get("state", "QUEUED")
    place = str(s.get("placement") or "")
    reason = str(s.get("reason") or "")
    if st in ("LEASED", "RUNNING"):
        return f"Working on {place}" if place else "Working"
    if st == "QUEUED":
        return "Queued"
    if st == "AWAITING-APPROVAL":
        return "Awaiting your approval"
    if st == "FAILED":
        return (f"{place} — failed" if place else "Failed") + (f": {s['error']}" if s.get("error") else "")
    via = " → ".join(m for m in (str(s.get("engine") or ""), place) if m and m != "None")
    dur = _dur(s.get("ts"), s.get("finished_ts"))
    return " · ".join(m for m in (via, reason, dur) if m)


def _preview(s: dict, limit: int = 160) -> str:
    """First beat of a finished step's result — the teaser, full text in the inspector."""
    res = str(s.get("result") or "").strip()
    if not res:
        return ""
    res = " ".join(res.split())
    return res[:limit].rstrip() + ("…" if len(res) > limit else "")


def work_main(run: dict | None, now: float, sel: str = "") -> str:
    if run is None:
        return """<div class="w-hero">
<h2 class="w-hero-t">What should the computer do?</h2>
<p class="muted note">Describe a goal on the left. It decomposes into steps you can watch
as it thinks — routed, placed, gated, worked. Nothing spends without your confirm.</p></div>"""
    subs = run["subtasks"]
    n = run["declared"] or len(subs)
    done = sum(1 for s in subs if s["state"] == "DONE")
    live = any(s["state"] in ("LEASED", "RUNNING", "QUEUED") for s in subs) or len(subs) < n
    elapsed = _dur(run.get("started"), (run.get("ended") or now) if run.get("started") else None)
    cost = sum(s.get("cost") or 0 for s in subs)
    head = (
        f'<div class="w-run-head"><h2 class="w-goal">{e(run["goal"] or run["run"])}</h2>'
        f'<p class="w-run-sub muted caption">{e(run["run"])}'
        + (f" · {e(rel_time(run['started'], now))}" if run.get("started") else "")
        + (f" · step {done}/{n}" if n else "")
        + (f" · {money(cost)}" if cost else "")
        + (f" · {e(elapsed)}" if elapsed else "")
        + f' · <a href="/goals/{e(run["run"])}">full page</a></p>'
        + (f'<progress class="w-prog" value="{done}" max="{max(n, 1)}"></progress>' if n else "")
        + "</div>"
    )
    run_err = ""
    if run.get("error"):
        kind = f'<span class="chip err-chip mono">{e(str(run["error_kind"]))}</span> ' if run.get("error_kind") else ""
        hint = f'<p class="muted caption w-hint">{e(str(run["hint"]))}</p>' if run.get("hint") else ""
        run_err = f'<div class="w-err" role="alert">{kind}{e(str(run["error"]))}{hint}</div>'
    plan = ""
    if subs:
        titles = "".join(f"<li>{e(s.get('title') or str(s.get('subtask') or ''))}</li>" for s in subs)
        plan = (f'<div class="w-thought"><header>{icon("neutral")}'
                f'<span>Planned {n or len(subs)} step{"s" if (n or len(subs)) != 1 else ""}</span></header>'
                f"<ol>{titles}</ol></div>")
    steps = []
    for i, s in enumerate(subs):
        cls, ic, verb = _STEP.get(s.get("state", "QUEUED"), _STEP["QUEUED"])
        cur = " sel" if str(i) == sel else ""
        kicker = _thought_line(s, now)
        kick_t = f'<span class="tl-kicker muted caption">{e(kicker)}</span>' if kicker else ""
        prev = _preview(s) if s.get("state") == "DONE" else ""
        prev_t = f'<span class="tl-preview">{e(prev)}</span>' if prev else ""
        steps.append(
            f'<li class="tl-step s-{cls}"><button type="button" class="tl-btn{cur}" data-sub="{i}">'
            f'<span class="tl-dot">{icon(ic)}</span><span class="tl-body">'
            f'<span class="tl-title">{e(s.get("title") or str(s.get("subtask") or ""))}</span>{kick_t}{prev_t}'
            "</span></button></li>"
        )
    queued = run["declared"] - len(subs)
    if queued > 0:
        steps.append(
            f'<li class="tl-step s-ghost"><span class="tl-dot"></span>'
            f'<span class="tl-body muted caption">+ {queued} queued behind dependencies</span></li>'
        )
    fails = "".join(_fail_item(f) for f in run["worker_failures"])
    fails_t = f'<h2>Worker failures</h2><ul class="w-fails">{fails}</ul>' if fails else ""
    return f'{head}{run_err}{plan}<ol class="tl">{"".join(steps)}</ol>{fails_t}'


def _doc_cls(s: str) -> str:
    """Model output that smells like code/data renders mono; prose renders doc."""
    t = s.strip()
    if t[:1] in "{[<`#!" or "```" in t or t.startswith(("$ ", "#!")):
        return " code"
    return ""


def work_inspect(run: dict | None, sel: str, pend: list[dict], now: float) -> str:
    out = []
    if pend:
        head = ('<thead><tr><th scope="col">Who</th><th scope="col">What</th><th scope="col">Goes to</th>'
                '<th scope="col" class="num">Cost</th><th scope="col">Risk</th><th scope="col" class="num">Asked</th>'
                '<th scope="col"><span class="sr">Actions</span></th></tr></thead>')
        table = ('<table class="dense approvals" data-table><caption class="sr">Pending approvals</caption>'
                 + head + "".join(approval_row(i, now) for i in pend) + "</table>")
        out.append(f'<h2>Needs you ({len(pend)})</h2>{table}')
    sub = None
    if run is not None and sel.isdigit():
        i = int(sel)
        if 0 <= i < len(run["subtasks"]):
            sub = run["subtasks"][i]
    if sub is not None:
        rows = "".join(f'<tr><th scope="row">{e(k)}</th><td>{v}</td></tr>' for k, v in [
            ("State", status(sub["state"])),
            ("Route", f'<span class="mono">{e(str(sub["route"]))}</span>' if sub.get("route") else ""),
            ("Engine", f'<span class="mono">{e(str(sub["engine"]))}</span>' if sub.get("engine") else ""),
            ("Placement", f'<span class="mono">{e(str(sub["placement"]))}</span>' if sub.get("placement") else ""),
            ("Why placed", e(str(sub["reason"])) if sub.get("reason") else ""),
            ("Duration", e(_dur(sub.get("ts"), sub.get("finished_ts"))) if sub.get("finished_ts") else ""),
            ("Gate", "approval required" if sub.get("gate_required") else ""),
            ("PII", e(", ".join(map(str, sub["pii"]))) if sub.get("pii") else ""),
            ("Cost", money(sub["cost"]) if sub.get("cost") is not None else ""),
        ] if v)
        res = str(sub["result"]) if sub.get("result") else ""
        err_t = ""
        if sub.get("error"):
            kind = f'<span class="chip err-chip mono">{e(str(sub["error_kind"]))}</span> ' if sub.get("error_kind") else ""
            hint = f'<p class="muted caption w-hint">{e(str(sub["hint"]))}</p>' if sub.get("hint") else ""
            err_t = f'<div class="w-err" role="alert">{kind}{e(str(sub["error"]))}{hint}</div>'
        title = sub.get("title") or str(sub.get("subtask") or "")
        doc = (f'<div class="w-doc{_doc_cls(res)}">{e(res)}</div>' if res
               else '<p class="muted caption">No result yet — the worker has not finished.</p>')
        out.append(f'<h2>Subtask</h2><div class="w-card"><p class="w-card-t">{e(title)}</p>'
                   f'<p class="w-card-sub muted caption">{_thought_line(sub, now)}</p>{err_t}{doc}'
                   f'<details class="w-details"><summary>Details</summary>'
                   f'<table class="kv dense"><tbody>{rows}</tbody></table></details></div>')
    elif run is not None:
        counts = {s["state"]: 0 for s in run["subtasks"]}
        for s in run["subtasks"]:
            counts[s["state"]] += 1
        queued = run["declared"] - len(run["subtasks"])
        if queued > 0:
            counts["QUEUED"] = counts.get("QUEUED", 0) + queued
        fails = "".join(_fail_item(f) for f in run["worker_failures"])
        run_err = ""
        if run.get("error"):
            kind = f'<span class="chip err-chip mono">{e(str(run["error_kind"]))}</span> ' if run.get("error_kind") else ""
            hint = f'<p class="muted caption w-hint">{e(str(run["hint"]))}</p>' if run.get("hint") else ""
            run_err = f'<div class="w-err" role="alert">{kind}{e(str(run["error"]))}{hint}</div>'
        out.append(f'<h2>Run summary</h2><div class="w-card"><p class="w-card-t">{e(run["run"])}</p>'
                   f'<p class="w-chips">{_state_chips(counts)}</p>' + run_err
                   + (f'<h3 class="w-sub-h">Worker failures</h3><ul class="w-fails">{fails}</ul>' if fails else "")
                   + "</div>")
    else:
        out.append(empty("Nothing selected",
                         "Pick a run on the left to see its plan; select a subtask for its output. "
                         "Pending approvals land here too."))
    return "".join(out)


def work_active(runs: list[dict], run: dict | None) -> bool:
    """True while anything can still move — drives work.js's poll cadence."""
    for r in runs:
        if any(r["states"].get(k) for k in ("QUEUED", "LEASED", "RUNNING", "AWAITING-APPROVAL")):
            return True
    if run:
        return (len(run["subtasks"]) < run["declared"]
                or any(s["state"] not in ("DONE", "FAILED") for s in run["subtasks"]))
    return False


# ------------------------------------------------------------- skills + automations


def page_skills(caps: list[dict]) -> str:
    """Capability registry — every row is a real code path with live health."""
    rows = "".join(
        f'<tr><td>{e(c["name"])}</td><td class="muted">{e(c["kind"])}</td>'
        f'<td>{status(c["status"])}</td><td class="muted">{e(c["detail"])}</td></tr>'
        for c in caps
    )
    return ("<section aria-labelledby=\"cap\"><h2 id=\"cap\">What a run can invoke</h2>"
            '<p class="muted note">The capability surface — each row is a real engine, worker or '
            'safety check. Health is probed live; a down engine is reported, not hidden. '
            'Placement picks among these per subtask.</p>'
            '<table class="dense"><caption class="sr">Capabilities and live health</caption><thead><tr>'
            '<th scope="col">Capability</th><th scope="col">Kind</th><th scope="col">Status</th>'
            '<th scope="col">Detail</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></section>')


def _auto_row(a: dict, csrf: str, now: float) -> str:
    sid = e(a["id"])
    nxt = ", ".join(time_tag(t, now) for t in a["next"][:2]) or '<span class="muted">—</span>'
    state = status("paused") if a["paused"] else status("active")
    toggle = ("Resume", "0") if a["paused"] else ("Pause", "1")
    return (
        f'<tr><td class="goal">{e(a["goal"] or "(no description)")}'
        f'<div class="muted caption mono">{sid}</div></td>'
        f'<td class="mono">{e(a["every"])}</td>'
        f'<td class="num">{nxt}</td><td class="num">{a["fired"]}</td><td>{state}</td>'
        '<td class="row-acts">'
        f'<form method="post" action="/automations/{sid}/pause" class="inline">'
        f'<input type="hidden" name="csrf" value="{e(csrf)}">'
        f'<input type="hidden" name="paused" value="{toggle[1]}">'
        f'<button class="btn ghost" type="submit">{toggle[0]}</button></form>'
        f'<form method="post" action="/automations/{sid}/trigger" class="inline">'
        f'<input type="hidden" name="csrf" value="{e(csrf)}">'
        f'<input type="hidden" name="desc" value="{e(a["goal"])}">'
        '<button class="btn ghost" type="submit">Run now</button></form>'
        f'<form method="post" action="/automations/{sid}/delete" class="inline">'
        f'<input type="hidden" name="csrf" value="{e(csrf)}">'
        f'<input type="hidden" name="desc" value="{e(a["goal"])}">'
        '<button class="btn ghost" type="submit">Delete</button></form>'
        '</td></tr>'
    )


def page_automations(csrf: str, items: list[dict] | None, now: float, error: str = "") -> str:
    """Temporal schedules: cadence, next fire, run-now/pause/delete."""
    err = f'<p class="err" role="alert">{e(error)}</p>' if error else ""
    form = f"""<section class="lit" aria-labelledby="nf"><h2 id="nf">New automation</h2>
<p class="muted note">A goal on a cadence. Every fire submits a real run — routed, placed, gated,
and driven to terminal state through the same protocol a manual goal uses.</p>
<form method="post" action="/automations" class="goal-form">
<input type="hidden" name="csrf" value="{e(csrf)}">
<label for="agoal" class="sr">Goal</label>
<textarea id="agoal" name="goal" class="input" rows="2" maxlength="4000" required
  placeholder="e.g. Summarize today's audit failures and post the digest"></textarea>
<div class="form-row">
<label for="every">Every</label>
<input id="every" name="every_minutes" type="number" class="input" min="5" max="10080" value="60" required>
<span class="muted">minutes</span>
<button type="submit" class="btn primary">Schedule it</button>
</div>
</form></section>"""
    if items is None:
        table = empty(
            "Scheduler unreachable",
            "Temporal isn't answering — schedules are durable state and nothing here will guess. "
            "Check the temporal stack and reload.")
        return form + err + table
    if not items:
        table = empty("No automations",
                      "Nothing is scheduled. Add one above — it fires real goal runs on a cadence.")
    else:
        rows = "".join(_auto_row(a, csrf, now) for a in items)
        table = ('<table class="dense"><caption class="sr">Automations</caption><thead><tr>'
                 '<th scope="col">Goal</th><th scope="col">Cadence</th>'
                 '<th scope="col" class="num">Next</th><th scope="col" class="num">Fired</th>'
                 '<th scope="col">State</th><th scope="col"></th></tr></thead>'
                 f'<tbody>{rows}</tbody></table>')
    return form + err + f"<section>{table}</section>"


def page_automation_confirm(csrf: str, sid: str, op: str, desc: str) -> str:
    """Confirm card for spendy/destructive automation actions (trigger, delete)."""
    verb = {"trigger": "Run it now", "delete": "Delete it"}.get(op, "Confirm")
    what = {
        "trigger": "Fires the schedule once, immediately — a real goal run that may spend.",
        "delete": "Removes the schedule permanently. Runs it already produced stay on disk.",
    }.get(op, "")
    return f"""<section class="confirm" aria-labelledby="ac"><h2 id="ac">{e(verb)}?</h2>
<table class="kv dense"><tbody>
<tr><th scope="row">Automation</th><td class="goal">{e(desc)}<div class="muted caption mono">{e(sid)}</div></td></tr>
<tr><th scope="row">Effect</th><td>{e(what)}</td></tr>
</tbody></table>
<form method="post" action="/automations/{e(sid)}/{e(op)}" class="actions">
<input type="hidden" name="csrf" value="{e(csrf)}">
<input type="hidden" name="confirm" value="1">
<button type="submit" class="btn primary">{e(verb)}</button>
<a class="btn" href="/automations">Cancel</a>
</form></section>"""


# ------------------------------------------------------------------- audit

AUDIT_FILTERS = (("all", "All"), ("blocked", "Blocked"), ("allowed", "Allowed"), ("decisions", "Decisions"))


def audit_match(r: dict, f: str, q: str) -> bool:
    ev = str(r.get("event", ""))
    if f == "blocked" and "blocked" not in ev:
        return False
    if f == "allowed" and "allowed" not in ev:
        return False
    if f == "decisions" and ev != "human_decision":
        return False
    return not q or q.lower() in json.dumps(r).lower()


def audit_state(r: dict) -> str:
    ev = str(r.get("event", ""))
    if "blocked" in ev:
        return status("blocked")
    if "allowed" in ev:
        return status("allowed")
    if ev == "human_decision":
        return status("approved" if r.get("approved") else "sent back")
    return status("neutral", "Recorded")


def audit_rows(rows: list[dict], now: float) -> str:
    out = []
    for r in rows:
        detail = {k: v for k, v in r.items() if k not in ("ts", "event")}
        text = " ".join(f"{k}={v}" for k, v in detail.items())
        out.append(
            f'<tr data-row><td class="num">{time_tag(r.get("ts", 0), now)}</td><td>{audit_state(r)}</td>'
            f'<td class="mono">{e(str(r.get("event", "")))}</td><td class="mono muted detail" title="{e(text)}">{e(text)}</td></tr>'
        )
    return "".join(out)


def page_audit(rows: list[dict], total: int, f: str, q: str, now: float) -> str:
    chips = "".join(
        f'<a class="chip" href="/audit?f={k}{"&q=" + quote(q) if q else ""}"'
        f'{" aria-current=true" if k == f else ""}>{lbl}</a>' for k, lbl in AUDIT_FILTERS
    )
    form = (f'<form method="get" action="/audit" class="inline-form"><input type="hidden" name="f" value="{e(f)}">'
            f'<label for="aq" class="sr">Filter audit rows</label>'
            f'<input id="aq" name="q" type="search" class="input" placeholder="Filter by text, session or model" value="{e(q)}" aria-keyshortcuts="/">'
            '<button class="btn" type="submit">Filter</button></form>')
    if not rows:
        body = empty("No matching events",
                     "The audit log is append-only. Blocked and allowed calls and human decisions appear here as they happen.")
    else:
        body = ('<table class="dense" data-table><caption class="sr">Audit events, newest first</caption><thead><tr>'
                '<th scope="col" class="num">When</th><th scope="col">Result</th><th scope="col">Event</th>'
                f'<th scope="col">Detail</th></tr></thead><tbody>{audit_rows(rows, now)}</tbody></table>')
    cap = f'<p class="muted caption">Showing the latest {len(rows)} of {total} matching events.</p>' if rows else ""
    return f'<div class="toolbar"><div class="chips" role="group" aria-label="Filter">{chips}</div>{form}</div>{body}{cap}'


# ---------------------------------------------------------------- overview


def page_overview(pend: list[dict], runs: list[dict], audit: list[dict],
                  spend: float, cap: float, now: float) -> str:
    if pend:
        rows = "".join(
            f'<tr data-row data-href="/approvals#{e(i["id"])}"><td class="who">{glint(i.get("who", "agent"), i.get("session", ""), i["ts"])}'
            f'<span>{e(i.get("who", "agent"))}</span></td><td class="what">{e(i.get("what", ""))}</td>'
            f'<td class="mono">{e(i["egress"])}</td><td class="num">{money(i.get("est_cost", 0))}</td>'
            f'<td>{status(risk_of(i)[0], risk_of(i)[0].title() + " risk")}</td><td class="num">{time_tag(i["ts"], now)}</td></tr>'
            for i in pend[:5]
        )
        needs = (f'<section class="lit" aria-labelledby="nm"><h2 id="nm">Needs you ({len(pend)})</h2>'
                 '<table class="dense" data-table><caption class="sr">Oldest pending approvals</caption><thead><tr><th scope="col">Who</th><th scope="col">What</th>'
                 '<th scope="col">Goes to</th><th scope="col" class="num">Cost</th><th scope="col">Risk</th><th scope="col" class="num">Asked</th></tr></thead>'
                 f'<tbody>{rows}</tbody></table><p><a class="btn primary" href="/approvals">Review {len(pend)} approval{"s" if len(pend) != 1 else ""}</a></p></section>')
    else:
        needs = ('<section aria-labelledby="nm"><h2 id="nm">Needs you</h2>'
                 + empty("Nothing waiting on you", "Requests from agents to send a payload off this box appear here.") + "</section>")
    last = runs[0] if runs else None
    last_s = (f'{status(last["status"])} <a href="/runs/{last["id"]}">{last["id"]}</a> {time_tag(last["ts"], now)}'
              if last else '<span class="muted">No runs yet</span>')
    summary = f"""<section aria-labelledby="st"><h2 id="st">Status</h2>
<table class="kv dense"><tbody>
<tr><th scope="row">Control plane and sandboxes</th><td data-frag="/_frag/status" aria-busy="true">{skeleton_rows(1, 1)}</td></tr>
<tr><th scope="row">Last matrix run</th><td>{last_s}</td></tr>
<tr><th scope="row">Router spend today</th><td>{money(spend)} of {money(cap)} daily cap</td></tr>
</tbody></table></section>"""
    recent = ('<section aria-labelledby="ra"><h2 id="ra">Recent audit</h2>'
              + (f'<table class="dense" data-table><caption class="sr">Latest audit events</caption><thead><tr><th scope="col" class="num">When</th><th scope="col">Result</th>'
                 f'<th scope="col">Event</th><th scope="col">Detail</th></tr></thead><tbody>{audit_rows(audit, now)}</tbody></table>'
                 '<p><a href="/audit">All audit events</a></p>' if audit else empty("No audit events yet", "Every gate decision is written here, append-only."))
              + "</section>")
    return needs + summary + recent


def status_cell(fleet: dict) -> str:
    if not fleet["configured"]:
        return status("not configured", "Not configured") + ' <span class="muted">Set CUBE_API_URL</span>'
    if not fleet["up"]:
        return status("down", "DOWN") + f' <span class="muted">{e(fleet["api_host"])} did not answer. Inventory unknown.</span>'
    n = len(fleet["sandboxes"])
    return status("up", "UP") + f' <span class="muted">{e(fleet["api_host"])} · </span><a href="/sandboxes">{n} sandbox{"es" if n != 1 else ""}</a>'


def not_found() -> str:
    return empty("Page not found", "That address is not part of the console.",
                 '<a class="btn primary" href="/">Go to Overview</a>')
