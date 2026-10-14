"""The workspace page: static shell only. Everything live is built by static/app.js from the event stream.

Server side renders just the frame (three columns, tab strip, composer) and embeds two JSON blocks: the action
registry (same one the console palette uses) and a small boot object. No inline script or style (strict CSP).
"""

from __future__ import annotations

import json

from services.opsview import registry, views

e = views.e
ASSET_V = "1"

TABS = [("progress", "Progress", "list"), ("sources", "Sources", "globe"), ("artifacts", "Artifacts", "doc"),
        ("changes", "Changes", "diff"), ("outbox", "Outbox", "outbox"), ("box", "Box", "cube"), ("cost", "Cost", "coin")]

EXTRA_ICONS = {
    "dot": '<circle cx="8" cy="8" r="3" class="fill"/>',
    "pause": '<path d="M5.5 3.5v9M10.5 3.5v9"/>',
    "stop": '<rect x="4" y="4" width="8" height="8" rx="1.5"/>',
    "send": '<path d="M2.5 8l11-5-4 10-2-4.2z"/>',
    "panel-left": '<rect x="2" y="3" width="12" height="10" rx="1.5"/><path d="M6 3v10"/>',
    "panel-right": '<rect x="2" y="3" width="12" height="10" rx="1.5"/><path d="M10 3v10"/>',
    "chev-right": '<path d="M6 3.5L10.5 8 6 12.5"/>',
    "chev-down": '<path d="M3.5 6L8 10.5 12.5 6"/>',
    "chev-left": '<path d="M10 3.5L5.5 8 10 12.5"/>',
    "plus": '<path d="M8 3v10M3 8h10"/>',
    "x": '<path d="M4 4l8 8M12 4l-8 8"/>',
    "list": '<path d="M3 4.5l1.3 1.3L6.5 3.5M3 9.5l1.3 1.3 2.2-2.3M8.5 5h5M8.5 10h5"/>',
    "globe": '<circle cx="8" cy="8" r="5.5"/><path d="M2.5 8h11M8 2.5c2 2 2 9 0 11M8 2.5c-2 2-2 9 0 11"/>',
    "doc": '<path d="M4 2.5h5l3 3v8H4z"/><path d="M9 2.5v3h3"/>',
    "diff": '<path d="M5.5 3v6M2.5 6h6M8 11.5h5.5"/>',
    "outbox": '<path d="M2.5 9v4h11V9M8 2.5v7M5 5.2L8 2.2l3 3"/>',
    "cube": '<path d="M8 1.8l5.5 3v6.4L8 14.2l-5.5-3V4.8zM2.5 4.8L8 8l5.5-3.2M8 8v6.2"/>',
    "coin": '<circle cx="8" cy="8" r="5.5"/><path d="M8 4.8v6.4M6.2 6.4c0-1.8 3.6-1.8 3.6 0 0 1.8-3.6 1-3.6 3 0 1.4 3.6 1.4 3.6-.2"/>',
    "check": '<path d="M3 8.5l3.2 3L13 4.5"/>',
    "circle": '<circle cx="8" cy="8" r="4.5"/>',
    "dash": '<path d="M4.5 8h7"/>',
    "file": '<path d="M4 2.5h5l3 3v8H4z"/>',
    "terminal": '<rect x="2" y="3" width="12" height="10" rx="1.5"/><path d="M4.8 6.5L7 8.2 4.8 9.9M8.5 10h2.7"/>',
    "lock": '<rect x="3.5" y="7" width="9" height="6.5" rx="1.5"/><path d="M5.5 7V5.2a2.5 2.5 0 015 0V7"/>',
    "expand": '<path d="M9.5 2.5h4v4M6.5 13.5h-4v-4M13.5 2.5L9 7M2.5 13.5L7 9"/>',
    "copy": '<rect x="5.5" y="5.5" width="8" height="8" rx="1.5"/><path d="M10.5 5.5V4a1.5 1.5 0 00-1.5-1.5H4A1.5 1.5 0 002.5 4v5A1.5 1.5 0 004 10.5h1.5"/>',
    "flag": '<path d="M4 14V2.5M4 3h7l-1.5 2.5L11 8H4"/>',
}


def sprite_extra() -> str:
    syms = "".join(f'<symbol id="i-{k}" viewBox="0 0 16 16">{v}</symbol>' for k, v in EXTRA_ICONS.items())
    return f'<svg width="0" height="0" class="sprite" aria-hidden="true" focusable="false">{syms}</svg>'


def icon(name: str, cls: str = "") -> str:
    return f'<svg class="ic {cls}" aria-hidden="true" focusable="false"><use href="#i-{name}"/></svg>'


def render(h, goal: str | None, code: int = 200) -> str:
    from . import routes
    w = routes.ws()
    boot = {"goal": goal, "replay": w.replay, "who": h._identity() or "", "tabs": [t[0] for t in TABS]}
    tabs = "".join(
        f'<button type="button" role="tab" id="tab-{k}" class="tab" data-tab="{k}" aria-controls="panel-{k}" '
        f'aria-selected="{"true" if k == "progress" else "false"}" tabindex="{"0" if k == "progress" else "-1"}" title="{label} ({i + 1})">'
        f'{icon(ic)}<span class="tab-label">{label}</span><kbd class="tab-key" aria-hidden="true">{i + 1}</kbd>'
        f'<span class="tab-badge" data-badge="{k}" hidden></span></button>'
        for i, (k, label, ic) in enumerate(TABS))
    panels = "".join(
        f'<section role="tabpanel" id="panel-{k}" class="panel" data-panel="{k}" aria-labelledby="tab-{k}" tabindex="-1"'
        f'{"" if k == "progress" else " hidden"}></section>' for k, _, _ in TABS)
    body_class = "has-goal" if goal else "no-goal"
    return f"""<!doctype html>
<html lang="en" data-page="workspace">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="csrf-token" content="{e(h._csrf())}">
<title>{"Not found" if code == 404 else "Workspace"} · open-computer</title>
<link rel="stylesheet" href="/static/silver-lining.css?v={ASSET_V}">
<link rel="stylesheet" href="/static/console.css?v={ASSET_V}">
<link rel="stylesheet" href="/static/ws/workspace.css?v={ASSET_V}">
<script src="/static/theme-init.js?v={ASSET_V}"></script>
</head>
<body class="ws-body {body_class}">
{views.sprite()}{sprite_extra()}
<a class="skip" href="#timeline">Skip to the conversation</a>
<div class="ws" id="ws" data-stream="idle" data-replay="{"1" if w.replay else "0"}" data-left="open" data-right="open">
<div class="ws-goals" id="goals-col" role="complementary" aria-label="Goals">
  <h1 class="sr" id="gl-h1" hidden>Goals</h1>
  <div class="gl-head">
    <a class="brand" href="/workspace">open-computer</a>
    <button type="button" class="btn ghost icon-btn" data-action="ws.new_goal" aria-label="New goal" aria-keyshortcuts="c">{icon("plus")}</button>
  </div>
  <div class="gl-search"><label class="sr" for="goal-search">Search goals</label>
    <input id="goal-search" class="input" type="search" placeholder="Search goals" autocomplete="off"></div>
  <nav id="goal-list" class="gl-list" aria-label="Goal list"></nav>
  <div class="gl-foot">
    <a class="btn ghost" href="/" data-action="nav.overview">Console<kbd>g o</kbd></a>
    <button type="button" class="btn ghost" data-action="theme">{icon("theme")}<span>Theme</span></button>
    <button type="button" class="btn ghost" data-action="help"><span>Shortcuts</span><kbd>?</kbd></button>
    <p class="muted caption">{e(h._identity() or "")}</p>
  </div>
</div>
<main class="ws-main" id="main" aria-label="Conversation">
  <header class="ws-title">
    <button type="button" class="btn ghost icon-btn" id="btn-left" data-action="ws.toggle_goals" aria-label="Toggle goal list" aria-expanded="true" aria-controls="goals-col" title="Goals ([)">{icon("panel-left")}</button>
    <a class="btn ghost icon-btn back-goals" href="/workspace" aria-label="All goals">{icon("chev-left")}</a>
    <h1 id="t-title" class="t-title" title="">Workspace</h1>
    <span id="t-status" class="chip-status"></span>
    <span id="t-elapsed" class="t-meta caption" title="Time from the run's first event to its latest"></span>
    <span class="grow"></span>
    <span id="t-spend" class="spend" role="group" aria-label="Spend"></span>
    <span id="t-ctx" class="ctx" role="img" aria-label="Context window"></span>
    <span id="t-replay" class="chip-replay" hidden title="Recorded run: no sandbox, no model calls">Replay</span>
    <button type="button" class="btn ghost" id="btn-stop" data-action="ws.stop" aria-label="Stop the run" aria-keyshortcuts="." hidden>{icon("stop")}<span class="hide-sm">Stop</span></button>
    <button type="button" class="btn ghost icon-btn" id="btn-right" data-action="ws.toggle_side" aria-label="Toggle sidebar" aria-expanded="true" aria-controls="side" title="Sidebar (])">{icon("panel-right")}</button>
  </header>
  <div class="ws-banner" id="banner" role="status" hidden></div>
  <div class="tl-wrap">
    <div id="timeline" class="ws-timeline" role="log" aria-live="off" aria-label="Conversation" tabindex="0" data-projection="timeline"></div>
    <button type="button" class="newpill" id="newpill" data-action="ws.reveal_new" hidden></button>
    <div class="empty-goal" id="empty-goal" hidden></div>
  </div>
  <div class="ws-foot" id="foot">
    <div id="dock" class="dock" aria-label="Needs your decision"></div>
    <div id="strip" class="strip" aria-label="Run summary"></div>
    <div id="queued" class="queued"></div>
    <form id="composer" class="composer" autocomplete="off">
      <label class="sr" for="composer-text">Message the agent</label>
      <textarea id="composer-text" rows="1" class="composer-text" placeholder="Message the agent, or describe a goal" aria-keyshortcuts="Control+Enter Meta+Enter"></textarea>
      <button type="submit" class="btn primary" id="composer-send" aria-keyshortcuts="Control+Enter">{icon("send")}<span>Send</span><kbd class="on-accent" aria-hidden="true">ctrl+enter</kbd></button>
    </form>
  </div>
  <div class="viewer-overlay" id="overlay" role="dialog" aria-modal="true" aria-label="Expanded view" hidden></div>
</main>
<aside class="ws-side" id="side" aria-label="Run context" data-snap="half">
  <button type="button" class="sheet-handle" id="sheet-handle" aria-label="Resize panel. Arrow keys change its height, Escape closes it." aria-expanded="false"><span></span></button>
  <div class="tabs" id="tabs" role="tablist" aria-label="Run context" aria-orientation="horizontal">{tabs}</div>
  <div class="panels" id="panels" data-projection="side">{panels}</div>
  <div class="resizer" id="resizer" role="separator" aria-orientation="vertical" aria-label="Resize sidebar" aria-valuemin="280" aria-valuemax="560" aria-valuenow="360" tabindex="0"></div>
</aside>
</div>
<div id="cite-pop" class="cite-pop" role="tooltip" hidden></div>
<div id="layers"></div>
<div id="toasts" class="toasts" role="status" aria-live="polite"></div>
<div id="alerts" class="toasts" role="alert" aria-live="assertive"></div>
<div id="announce" class="sr" role="status" aria-live="polite"></div>
<script type="application/json" id="oc-actions">{registry.payload("workspace")}</script>
<script type="application/json" id="ws-boot">{json.dumps(boot).replace("</", "<\\/")}</script>
<script type="module" src="/static/ws/app.js?v={ASSET_V}"></script>
</body>
</html>"""
