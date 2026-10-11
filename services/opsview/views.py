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
    "overview": "Overview", "goals": "Goals", "sandboxes": "Sandboxes", "runs": "Runs",
    "approvals": "Approvals", "router": "Router", "audit": "Audit",
}
NAV_PAGE = {a.id.split(".", 1)[1]: a for a in registry.nav_actions()}

ASSET_V = "1"

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
        for k in ("overview", "approvals", "runs", "sandboxes")
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
  <button type="button" class="btn palette-btn" data-action="palette" aria-label="Open command palette">{icon("search")}<span class="hide-sm">Search or run a command</span><kbd>ctrl+k</kbd></button>
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
    fails = "".join(
        f'<li><span class="mono">{e(str(f.get("task", "")))}</span> — {e(str(f.get("error", "")))}</li>'
        for f in run["worker_failures"])
    fails_t = (f'<h2>Worker failures</h2><ul class="task-list">{fails}</ul>' if fails else "")
    refresh = ' data-refresh="3"' if active else ""
    pending_note = (f'<p class="muted caption">{run["declared"] - len(subs)} subtask(s) still queued '
                    'behind dependencies.</p>' if len(subs) < run["declared"] else "")
    head = (f'<p class="muted note">{e(run["goal"])}'
            + (f' · started {time_tag(run["started"], now)}' if run.get("started") else "")
            + '</p>')
    return (f'<section{refresh}>{head}<ul class="task-list">{"".join(items)}</ul>'
            f'{pending_note}{fails_t}</section>')


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
