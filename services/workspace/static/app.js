// Agent workspace: boot, event stream, render loop, layout. Every view is a projection of the goal's event stream
// (projection.js), so a reload or a replay rebuilds exactly the same screen.
import { $, $$, el, icon, store, money, dur, reconcile, absTime, num } from "./util.js";
import { ctx, csrf } from "./ctx.js";
import { apply, list, pendingApprovals, spentPct, contextPct, elapsed, isPausedForCap, groupOf, summary } from "./projection.js";
import { timelineItems } from "./timeline.js";
import { renderSide, RENDER } from "./side.js";
import { buildCard, buildCapCard, sigOf, wireCard, chip, fullView } from "./approval.js";
import { openViewer } from "./viewers.js";
import { installKeys, toast, announce, openOverlay, closeOverlay, escape as escapeLayer } from "./keys.js";
import { createState } from "./projection.js";

const FLUSH_MS = 250; // at most 4 renders a second
const q = new URLSearchParams(location.search);
const ws = $("#ws");
ctx.events = [];
ctx.toast = toast;
ctx.announce = announce;
ctx.openOverlay = openOverlay;

// ---- network ---------------------------------------------------------------------------------------------------
ctx.post = (url, data) =>
  fetch(url, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", Accept: "application/json", "X-CSRF-Token": csrf() }, body: JSON.stringify(data || {}) })
    .then((r) => r.json().then((body) => ({ ok: r.ok, status: r.status, body })).catch(() => ({ ok: r.ok, status: r.status, body: {} })))
    .then((res) => { if (res.ok && ended && /\/(messages|export|decide)$/.test(url)) reopen(); return res; });
// A finished goal's stream is closed. Acting on it (a message, an export request) reopens it from the last seq seen.
function reopen() { ended = false; if (es) es.close(); connect(ctx.state.lastSeq); }

// ---- citations: chips in chat <-> rows in Sources ----------------------------------------------------------------
const pop = $("#cite-pop");
let citeReturn = null;
ctx.mdCtx = {
  onCite(ns, btn) {
    citeReturn = btn;
    ctx.ui.highlightSource = ns[0];
    setTab("sources", false);
    const row = $(`[data-src-btn="${ns[0]}"]`);
    if (row) { row.scrollIntoView({ block: "nearest" }); row.focus({ preventScroll: true }); }
    openSideIfClosed();
  },
  showCite(ns, btn) {
    const s = ctx.state;
    const srcs = ns.map((n) => list(s.sources).find((x) => x.n === n)).filter(Boolean);
    if (!srcs.length) return;
    pop.textContent = "";
    srcs.forEach((x) => {
      const r = el("div", "pop-row");
      r.appendChild(el("span", "tile", (x.domain || "?").replace(/^www\./, "").charAt(0).toUpperCase(), { "aria-hidden": "true" }));
      const c = el("div", "pop-col"); c.appendChild(el("strong", "", x.title)); c.appendChild(el("span", "mono caption muted", x.domain || ""));
      const note = btn.closest("[data-msg]");
      const n = note && s.notes.get(note.dataset.msg);
      const quote = n && (n.cites || []).find((cc) => cc.n === x.n);
      c.appendChild(el("span", "pop-q caption", "“" + ((quote && quote.quote) || x.snippet || "") + "”"));
      if (!x.fetched) c.appendChild(el("span", "caption", "Not verified: cited without being fetched."));
      r.appendChild(c); pop.appendChild(r);
    });
    pop.hidden = false;
    const b = btn.getBoundingClientRect(), pw = 300;
    pop.style.left = Math.max(8, Math.min(innerWidth - pw - 8, b.left)) + "px";
    pop.style.top = Math.min(innerHeight - pop.offsetHeight - 8, b.bottom + 6) + "px";
    btn.setAttribute("aria-describedby", "cite-pop");
  },
  hideCite() { pop.hidden = true; },
  onFile(ref) { openFile(ref); },
};
export function citeReturnFocus() { if (citeReturn && document.contains(citeReturn)) { citeReturn.focus(); citeReturn = null; return true; } return false; }
ctx.citeReturnFocus = citeReturnFocus;

function openFile(ref) {
  const m = /^(.*):(\d+)$/.exec(ref); if (!m) return;
  const ch = list(ctx.state.changes).find((c) => c.path === m[1] || c.path.endsWith("/" + m[1]) || m[1].endsWith(c.path));
  openSideIfClosed();
  if (ch) {
    ctx.ui.openFiles.add(ch.id); setTab("changes", false); doRender();
    const row = $(`[data-change="${ch.id}"] .dl[data-new="${m[2]}"]`);
    if (row) { row.scrollIntoView({ block: "center" }); row.classList.add("hit"); }
    return;
  }
  const art = list(ctx.state.artifacts).find((a) => a.name === m[1].split("/").pop());
  if (art) { openArtifactInPanel(art.id); return; }
  toast(`${m[1]} isn't in Changes or Artifacts yet.`);
}

// ---- viewer context ----------------------------------------------------------------------------------------------------
ctx.viewerCtx = () => ({
  mdCtx: ctx.mdCtx,
  stepTitle: (id) => (ctx.state.steps.get(id) || {}).title || "",
  exportApproval: (a) => ctx.state.approvals.get("ap_exp_" + a.id) || null,
});

function openArtifactInPanel(id) {
  const a = ctx.state.artifacts.get(id); if (!a) return;
  if (isPhone()) { expandArtifact(id); return; } // phones: artifacts open full screen with a back chevron
  ctx.ui.panelArtifact = id; a.unreviewed = false;
  setTab("artifacts", false); openSideIfClosed(); doRender();
  const b = $("#panel-artifacts .art-view-bar button"); if (b) b.focus({ preventScroll: true });
}
ctx.openArtifactInPanel = openArtifactInPanel;

let overlayArtifact = null;
export function expandArtifact(id) {
  const a = ctx.state.artifacts.get(id || ctx.ui.panelArtifact); if (!a) return;
  overlayArtifact = a.id; a.unreviewed = false;
  const ov = $("#overlay"); ov.textContent = ""; ov.hidden = false;
  const bar = el("div", "ov-bar");
  bar.appendChild(el("h2", "ov-title mono", a.name));
  const phone = isPhone();
  const close = el("button", "btn" + (phone ? " ghost" : ""), phone ? "Back" : "Close", { type: "button", "data-act": "ov-close" });
  if (phone) close.prepend(icon("chev-left")); else close.appendChild(el("kbd", "", "esc"));
  if (phone) bar.prepend(close); else bar.appendChild(close);
  ov.appendChild(bar);
  const body = el("div", "ov-body"); body.appendChild(openViewer(a, ctx.viewerCtx())); ov.appendChild(body);
  openOverlay(close);
}
ctx.expandArtifact = expandArtifact;

// ---- tabs and side ----------------------------------------------------------------------------------------------------
export function setTab(name, focus) {
  ctx.ui.tab = name;
  ctx.persist("tab", name);
  $$("[data-tab]").forEach((t) => { const on = t.dataset.tab === name; t.setAttribute("aria-selected", on ? "true" : "false"); t.tabIndex = on ? 0 : -1; });
  $$("[data-panel]").forEach((p) => { p.hidden = p.dataset.panel !== name; });
  doRender();
  if (focus) $(`#tab-${name}`).focus();
}
ctx.setTab = setTab;
$("#tabs").addEventListener("click", (e) => { const t = e.target.closest("[data-tab]"); if (t) setTab(t.dataset.tab, false); });
$("#tabs").addEventListener("keydown", (e) => {
  const tabs = $$("[data-tab]"); const i = tabs.indexOf(document.activeElement);
  if (i < 0) return;
  let n = -1;
  if (e.key === "ArrowRight") n = (i + 1) % tabs.length; else if (e.key === "ArrowLeft") n = (i - 1 + tabs.length) % tabs.length;
  else if (e.key === "Home") n = 0; else if (e.key === "End") n = tabs.length - 1;
  if (n >= 0) { e.preventDefault(); e.stopPropagation(); setTab(tabs[n].dataset.tab, true); }
});

function openSideIfClosed() {
  if (isPhone()) { if (ws.dataset.sheet !== "open") setSheet("open", "half"); return; }
  if (ws.dataset.right === "closed") toggleSide(true);
}
const isPhone = () => matchMedia("(max-width: 767px)").matches;

export function toggleSide(force) {
  if (isPhone()) { setSheet(ws.dataset.sheet === "open" ? "closed" : "open", "half"); return; }
  const open = force != null ? force : ws.dataset.right === "closed";
  ws.dataset.right = open ? "open" : "closed";
  $("#btn-right").setAttribute("aria-expanded", String(open));
  store("oc-ws:right", ws.dataset.right);
}
export function toggleGoals(force) {
  const open = force != null ? force : ws.dataset.left === "closed";
  ws.dataset.left = open ? "open" : "closed";
  $("#btn-left").setAttribute("aria-expanded", String(open));
  store("oc-ws:left", ws.dataset.left);
}
ctx.toggleSide = toggleSide; ctx.toggleGoals = toggleGoals;

// bottom sheet (phones): peek 30%, half, full; handle, swipe down to close
const SNAPS = ["peek", "half", "full"];
export function setSheet(state, snap) {
  ws.dataset.sheet = state;
  if (snap) $("#side").dataset.snap = snap;
  $("#sheet-handle").setAttribute("aria-expanded", String(state === "open"));
  if (state === "open") { const t = $(`#tab-${ctx.ui.tab}`); if (t && !$("#side").contains(document.activeElement)) t.focus({ preventScroll: true }); }
}
ctx.setSheet = setSheet; ctx.sheetOpen = () => ws.dataset.sheet === "open";
{
  const h = $("#sheet-handle"); let y0 = null;
  h.addEventListener("pointerdown", (e) => { y0 = e.clientY; h.setPointerCapture(e.pointerId); });
  h.addEventListener("pointerup", (e) => {
    if (y0 == null) return; const dy = e.clientY - y0; y0 = null;
    const i = SNAPS.indexOf($("#side").dataset.snap);
    if (Math.abs(dy) < 8) { setSheet("open", SNAPS[(i + 1) % 3]); return; }
    if (dy > 40) { if (i === 0) setSheet("closed"); else setSheet("open", SNAPS[i - 1]); }
    else if (dy < -40) setSheet("open", SNAPS[Math.min(i + 1, 2)]);
  });
  h.addEventListener("keydown", (e) => {
    const i = SNAPS.indexOf($("#side").dataset.snap);
    if (e.key === "ArrowUp") { e.preventDefault(); setSheet("open", SNAPS[Math.min(i + 1, 2)]); }
    else if (e.key === "ArrowDown") { e.preventDefault(); if (i === 0) setSheet("closed"); else setSheet("open", SNAPS[i - 1]); }
  });
}

// resizable sidebar, 280..560
{
  const r = $("#resizer"); let drag = false;
  const set = (w) => { w = Math.max(280, Math.min(560, Math.round(w))); ws.style.setProperty("--side-width", w + "px"); r.setAttribute("aria-valuenow", String(w)); store("oc-ws:side-width", String(w)); };
  const saved = Number(store("oc-ws:side-width")); if (saved) set(saved);
  r.addEventListener("pointerdown", (e) => { drag = true; r.setPointerCapture(e.pointerId); });
  r.addEventListener("pointermove", (e) => { if (drag) set(innerWidth - e.clientX); });
  r.addEventListener("pointerup", () => { drag = false; });
  r.addEventListener("keydown", (e) => { const w = Number(r.getAttribute("aria-valuenow")); if (e.key === "ArrowLeft") { e.preventDefault(); set(w + 16); } else if (e.key === "ArrowRight") { e.preventDefault(); set(w - 16); } });
}

// ---- stream ---------------------------------------------------------------------------------------------------------------
let queue = [], timer = 0, lastFlush = 0, es = null, connected = false, wasConnected = false, ended = false;
function setStream(v) { ws.dataset.stream = v; }
function schedule() {
  if (timer) return;
  const wait = Math.max(0, FLUSH_MS - (performance.now() - lastFlush));
  timer = setTimeout(flush, wait);
}
function flush() {
  timer = 0; lastFlush = performance.now();
  if (!queue.length) return;
  const batch = queue; queue = [];
  const before = pendingApprovals(ctx.state).map((a) => a.id);
  const wasStatus = ctx.state.run.status;
  for (const ev of batch) if (apply(ctx.state, ev)) ctx.events.push(ev);
  const after = pendingApprovals(ctx.state);
  const fresh = after.filter((a) => !before.includes(a.id));
  if (fresh.length) announce("Approval needed: " + fresh[0].action);
  const st = ctx.state.run.status;
  if (st !== wasStatus && ["done", "failed", "cancelled"].includes(st)) announce("Run " + st);
  if (isPausedForCap(ctx.state) && wasStatus !== "paused") announce("Paused at the spend cap");
  doRender();
  if (ended && !queue.length) setStream("complete");
}
function connect(after) {
  if (!ctx.goal) return;
  setStream("connecting");
  const qs = [];
  if (after) qs.push("last_event_id=" + after);
  if (q.get("hb")) qs.push("hb=" + encodeURIComponent(q.get("hb")));
  es = new EventSource(`/goals/${ctx.goal}/events` + (qs.length ? "?" + qs.join("&") : ""));
  // Data events are unnamed (type is in the payload): an SSE event named "error" would collide with the
  // connection-level error event and look like a disconnect.
  es.onmessage = (e) => { queue.push(JSON.parse(e.data)); schedule(); };
  es.addEventListener("end", () => { ended = true; es.close(); connected = false; setStream(queue.length ? "streaming" : "complete"); schedule(); setBanner(); });
  es.addEventListener("reset", () => { ctx.events = []; Object.assign(ctx.state, freshState()); queue = []; doRender(); });
  es.onopen = () => {
    const re = wasConnected; connected = true; wasConnected = true; setStream("streaming"); setBanner();
    if (re) sweepApprovals();
  };
  es.onerror = () => { if (ended) return; connected = false; setBanner(); setStream("disconnected"); };
}
const freshState = () => createState(ctx.goal);

function sweepApprovals() {
  // On reconnect the stream resumes after the last seq it saw; this also checks the authoritative list so no
  // pending approval is ever missed.
  fetch(`/goals/${ctx.goal}/approvals`, { credentials: "same-origin" }).then((r) => r.json()).then((j) => {
    const mine = new Map(list(ctx.state.approvals).map((a) => [a.id, a.status]));
    if ((j.approvals || []).some((a) => mine.get(a.id) !== a.status)) toast("Caught up on approvals after reconnecting.");
  }).catch(() => {});
}

function setBanner() {
  const b = $("#banner");
  if (!ctx.goal) { b.hidden = true; return; }
  if (!connected && !ended && wasConnected) { b.hidden = false; b.className = "ws-banner warn"; b.textContent = "Disconnected from the event stream. Reconnecting; nothing is lost, the stream resumes where it stopped."; }
  else if (ctx.boot.replay) { b.hidden = false; b.className = "ws-banner"; b.textContent = ""; b.appendChild(el("span", "", "Replay: a recorded run. No sandbox and no model.")); const r = el("button", "btn ghost sm hide-sm", "Restart replay", { type: "button", "data-action": "ws.restart_replay" }); b.appendChild(r); }
  else b.hidden = true;
}

// ---- chrome ----------------------------------------------------------------------------------------------------------------
const STATUS = {
  queued: ["neutral", "neutral", "Queued"], planning: ["neutral", "neutral", "Planning"], running: ["healthy", "dot", "Running"],
  waiting: ["attention", "attention", "Waiting"], paused: ["attention", "pause", "Paused"], done: ["healthy", "ok", "Done"],
  failed: ["urgent", "urgent", "Failed"], cancelled: ["neutral", "stop", "Stopped"],
};
export function statusChip(s) {
  const pend = pendingApprovals(s).length;
  let key = s.run.status;
  let label = STATUS[key][2];
  let cls = STATUS[key][0], ic = STATUS[key][1];
  if (isPausedForCap(s)) label = "Paused at cap";
  else if (pend && !["done", "failed", "cancelled"].includes(key)) { cls = "attention"; ic = "attention"; label = "Needs you"; }
  return chip(cls, ic, label);
}

function ring(pct) {
  const NS = "http://www.w3.org/2000/svg", r = 7, C = 2 * Math.PI * r;
  const svg = document.createElementNS(NS, "svg"); svg.setAttribute("viewBox", "0 0 20 20"); svg.setAttribute("class", "ring"); svg.setAttribute("aria-hidden", "true");
  const bg = document.createElementNS(NS, "circle"); bg.setAttribute("cx", "10"); bg.setAttribute("cy", "10"); bg.setAttribute("r", String(r)); bg.setAttribute("class", "ring-bg");
  const fg = document.createElementNS(NS, "circle"); fg.setAttribute("cx", "10"); fg.setAttribute("cy", "10"); fg.setAttribute("r", String(r)); fg.setAttribute("class", "ring-fg" + (pct >= 0.85 ? " hot" : ""));
  fg.setAttribute("stroke-dasharray", `${(C * Math.min(pct, 1)).toFixed(2)} ${C.toFixed(2)}`); fg.setAttribute("transform", "rotate(-90 10 10)");
  svg.appendChild(bg); svg.appendChild(fg); return svg;
}

function renderChrome() {
  const s = ctx.state, r = s.run;
  const title = r.title || (ctx.goal ? "Goal" : "Workspace");
  const h1 = $("#t-title"); if (h1.textContent !== title) { h1.textContent = title; h1.title = title; }
  const sc = $("#t-status"); const sig = [r.status, r.reason, pendingApprovals(s).length].join("|");
  if (sc.dataset.sig !== sig) { sc.dataset.sig = sig; sc.textContent = ""; if (ctx.goal) sc.appendChild(statusChip(s)); }
  $("#t-elapsed").textContent = ctx.goal && r.startedTs != null ? dur(elapsed(s)) : "";
  // spend/cap chip and context ring: always visible, whichever tab is open
  const sp = $("#t-spend"); const pct = spentPct(s);
  const ssig = [s.cost.spent, s.cost.cap].join("|");
  if (sp.dataset.sig !== ssig) {
    sp.dataset.sig = ssig; sp.textContent = "";
    if (ctx.goal && (s.cost.cap || s.cost.spent)) {
      sp.className = "spend" + (pct >= 1 ? " full" : pct >= 0.8 ? " warn" : "");
      sp.appendChild(el("progress", "bar", null, { max: "100", value: String(Math.min(100, Math.round(pct * 100))), "aria-label": "Spend against the cap", tabindex: "-1" }));
      sp.appendChild(el("span", "spend-txt num", `${money(s.cost.spent)} / ${money(s.cost.cap)}`));
      if (pct >= 0.8) sp.appendChild(el("span", "sr", pct >= 1 ? "Cap reached" : "Over 80% of the cap"));
    }
  }
  const cx = $("#t-ctx"); const cp = contextPct(s); const csig = String(Math.round(cp * 100));
  if (cx.dataset.sig !== csig) {
    cx.dataset.sig = csig; cx.textContent = "";
    if (ctx.goal && s.cost.context.window) { cx.appendChild(ring(cp)); cx.appendChild(el("span", "caption muted num", csig + "%")); cx.setAttribute("aria-label", `Context window ${csig}% full, ${num(s.cost.context.used)} of ${num(s.cost.context.window)} tokens`); cx.title = cx.getAttribute("aria-label"); }
  }
  $("#t-replay").hidden = !(ctx.boot.replay && ctx.goal);
  const live = ctx.goal && ["running", "planning", "waiting", "paused", "queued"].includes(r.status) && s.count > 0;
  $("#btn-stop").hidden = !live;
  const pend = pendingApprovals(s).length;
  document.title = (pend ? `(${pend}) ` : "") + (r.title ? r.title + " · " : "Workspace · ") + "open-computer";
  renderStrip();
}

function renderStrip() {
  const s = ctx.state, strip = $("#strip");
  const pend = pendingApprovals(s).length;
  const items = [
    { key: "status", sig: [s.run.status, s.run.reason, pend].join("|"), build() { const d = el("span", "strip-chip static"); d.appendChild(statusChip(s)); return d; } },
    { key: "spend", sig: s.cost.spent + "/" + s.cost.cap, build() { return el("span", "strip-chip static num", `${money(s.cost.spent)} / ${money(s.cost.cap)}`); } },
    ...[["outbox", "Outbox", pend], ["sources", "Sources", s.sources.size], ["artifacts", "Artifacts", s.artifacts.size]].map(([k, label, n]) => ({
      key: k, sig: String(n) + (k === "outbox" && pend ? "!" : ""), build() {
        const b = el("button", "strip-chip" + (k === "outbox" && pend ? " attn" : ""), `${label} ${n}`, { type: "button", "data-strip": k, "data-fid": "strip-" + k });
        return b;
      },
    })),
  ];
  if (!ctx.goal) { strip.textContent = ""; return; }
  reconcile(strip, items);
}

// ---- timeline --------------------------------------------------------------------------------------------------------------
const tl = $("#timeline"), pill = $("#newpill");
tl.addEventListener("scroll", () => { ctx.ui.follow = tl.scrollHeight - tl.scrollTop - tl.clientHeight < 48; if (ctx.ui.follow && pill && !pill.hidden) revealNew(); });
export function revealNew() {
  ctx.ui.revealed = Infinity; doRender(); tl.scrollTop = tl.scrollHeight; ctx.ui.follow = true;
}
ctx.revealNew = revealNew;
function renderTimeline() {
  const items = timelineItems();
  const follow = ctx.ui.follow;
  if (follow) ctx.ui.revealed = items.length;
  const shown = items.slice(0, Math.min(ctx.ui.revealed, items.length));
  reconcile(tl, shown);
  const hidden = items.length - shown.length;
  pill.hidden = hidden <= 0;
  if (hidden > 0) { pill.textContent = ""; pill.appendChild(icon("chev-down")); pill.appendChild(el("span", "", `${hidden} new`)); pill.setAttribute("aria-label", `${hidden} new items. Show them.`); }
  if (follow && tl.scrollHeight > tl.clientHeight) tl.scrollTop = tl.scrollHeight;
  const e = $("#empty-goal");
  e.hidden = !!ctx.goal;
}

// ---- dock (approvals and cap pause) ---------------------------------------------------------------------------------------
function renderDock() {
  const dock = $("#dock"), s = ctx.state, items = [];
  const hadFocus = dock.contains(document.activeElement);
  void hadFocus;
  if (isPausedForCap(s)) {
    items.push({ key: "cap", sig: [s.cost.spent, s.cost.cap, s.run.status].join("|"), build() {
      const c = buildCapCard();
      c.querySelector("form").addEventListener("submit", capSubmit);
      return c;
    } });
  }
  const pend = pendingApprovals(s);
  const live = pend.filter((a) => !ctx.ui.deferred.has(a.id));
  const deferred = pend.filter((a) => ctx.ui.deferred.has(a.id));
  if (live.length) {
    const a = live[0];
    items.push({ key: "card:" + a.id, sig: sigOf(a), build() { const c = buildCard(a); wireCard(c, a); return c; } });
    if (live.length > 1) items.push({ key: "more", sig: String(live.length), build() { return el("p", "dock-more caption muted", `${live.length - 1} more waiting after this one. Oldest first.`); } });
  }
  if (deferred.length) items.push({ key: "deferred", sig: deferred.map((a) => a.id).join(","), build() {
    const d = el("div", "dock-deferred");
    d.appendChild(icon("attention")); d.appendChild(el("span", "", `${deferred.length} deferred. Nothing was decided.`));
    d.appendChild(el("button", "btn ghost sm", "Review", { type: "button", "data-act": "undefer", "data-fid": "undefer" }));
    return d;
  } });
  reconcile(dock, items);
  dock.dataset.count = String(items.length);
}
async function capSubmit(e) {
  e.preventDefault();
  const v = Number($("#cap-input").value);
  const r = await ctx.post(`/goals/${ctx.goal}/resume`, { cap_usd: v });
  if (!r.ok) toast(r.body.error || "Couldn't raise the cap.", true); else announce("Cap raised to " + money(v) + ". Resuming.");
}
$("#dock").addEventListener("click", (e) => {
  const b = e.target.closest("[data-act]"); if (!b) return;
  if (b.dataset.act === "undefer") { ctx.ui.deferred.clear(); doRender(); const c = $("#dock .acard"); if (c) c.focus(); }
});
export function focusApproval(id) {
  if (id) ctx.ui.deferred.delete(id); else ctx.ui.deferred.clear();
  doRender();
  const c = $("#dock .acard"); if (c) { c.focus(); c.scrollIntoView({ block: "nearest" }); return true; }
  return false;
}
ctx.focusApproval = focusApproval;

// ---- goal list --------------------------------------------------------------------------------------------------------------
let goals = [];
const GROUPS = [["needs_you", "Needs you"], ["running", "Running"], ["done", "Done"], ["failed", "Failed"]];
let filter = "";
function goalRow(g) {
  const mine = g.id === ctx.goal;
  const sm = mine && ctx.state.count ? { ...g, status: ctx.state.run.status, group: groupOf(ctx.state), spent_usd: ctx.state.cost.spent, cap_usd: ctx.state.cost.cap, pending_approvals: pendingApprovals(ctx.state).length, reason: ctx.state.run.reason } : g;
  return sm;
}
export function renderGoals() {
  const host = $("#goal-list"); const f = filter.trim().toLowerCase();
  const rows = goals.map(goalRow).filter((g) => !f || (g.title + " " + g.status).toLowerCase().includes(f));
  const items = [];
  for (const [k, label] of GROUPS) {
    const gs = rows.filter((g) => g.group === k);
    if (!gs.length) continue;
    items.push({ key: "h:" + k, sig: label + gs.length, build() { const h = el("h2", "gl-group", label); h.appendChild(el("span", "caption muted num", String(gs.length))); return h; } });
    gs.forEach((g) => items.push({ key: "g:" + g.id, sig: [g.title, g.status, g.reason, g.spent_usd, g.pending_approvals, g.id === ctx.goal].join("|"), build() {
      const a = el("a", "gl-row" + (g.id === ctx.goal ? " cur" : ""), null, { href: "/workspace/" + g.id, "data-goal": g.id, "data-fid": "goal-" + g.id, "data-row-btn": "" });
      if (g.id === ctx.goal) a.setAttribute("aria-current", "page");
      a.appendChild(el("span", "gl-title", g.title || g.id));
      const meta = el("span", "gl-meta");
      const isCap = g.status === "paused" && g.reason === "cap";
      const lab = isCap ? "Paused at cap" : g.pending_approvals ? "Needs approval" : (g.status[0].toUpperCase() + g.status.slice(1));
      const cls = g.group === "failed" ? "urgent" : g.group === "needs_you" ? "attention" : g.group === "done" ? "healthy" : "neutral";
      const ic = g.group === "failed" ? "urgent" : g.group === "needs_you" ? (isCap ? "pause" : "attention") : g.group === "done" ? "ok" : g.status === "running" ? "dot" : "neutral";
      meta.appendChild(chip(cls, ic, lab));
      if (g.cap_usd) meta.appendChild(el("span", "caption muted num", `${money(g.spent_usd)} / ${money(g.cap_usd)}`));
      a.appendChild(meta);
      return a;
    } }));
  }
  if (!rows.length) items.push({ key: "none", sig: f, build() { return el("p", "muted caption gl-none", f ? "No goal matches." : "No goals yet. Describe one in the composer."); } });
  reconcile(host, items);
}
async function loadGoals() {
  try {
    const r = await fetch("/goals.json", { credentials: "same-origin" }); const j = await r.json();
    const prev = new Map(goals.map((g) => [g.id, g]));
    goals = j.goals;
    for (const g of goals) {
      const p = prev.get(g.id);
      if (p && g.id !== ctx.goal && g.group === "needs_you" && p.group !== "needs_you") {
        const k = `oc-notified:${g.id}:${g.last_seq}`;
        if (!store(k)) { store(k, "1"); toast(`${g.title} needs you.`); }
      }
    }
    renderGoals();
  } catch (e) { /* offline: keep the last list */ }
}
$("#goal-search").addEventListener("input", (e) => { filter = e.target.value; renderGoals(); });

// ---- composer --------------------------------------------------------------------------------------------------------------------
const ta = $("#composer-text");
function autosize() { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 160) + "px"; }
ta.addEventListener("input", () => { autosize(); });
{
  const key = "oc-ws:draft:" + (ctx.goal || "new");
  const saved = store(key); if (saved) { ta.value = saved; autosize(); }
  let last = ta.value;
  setInterval(() => { if (ta.value !== last) { last = ta.value; store(key, ta.value || null); } }, 2000); // drafts autosave every 2s
}
$("#composer").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = ta.value.trim(); if (!text) return;
  const key = "oc-ws:draft:" + (ctx.goal || "new");
  if (!ctx.goal) {
    const r = await ctx.post("/workspace/new", { text });
    if (!r.ok) return toast(r.body.error || "Couldn't start that goal.", true);
    store(key, null); store("oc-ws:notice", r.body.notice || "");
    location.href = "/workspace/" + r.body.id + (q.get("speed") != null ? "?speed=" + q.get("speed") : "");
    return;
  }
  const qd = el("div", "queued-msg"); qd.appendChild(el("span", "caption muted", "Queued: ")); qd.appendChild(el("span", "", text.length > 120 ? text.slice(0, 119) + "…" : text)); qd.dataset.text = text;
  $("#queued").appendChild(qd);
  ta.value = ""; autosize(); store(key, null);
  const r = await ctx.post(`/goals/${ctx.goal}/messages`, { text });
  if (!r.ok) { qd.remove(); ta.value = text; autosize(); toast(r.body.error || "Couldn't send that.", true); }
  else if (ctx.boot.replay) toast("Replay mode: your message is logged, but a recorded run can't answer it.");
});
// queued messages clear once the stream shows them as notes
function clearQueued() {
  const texts = new Set(list(ctx.state.notes).filter((n) => n.role === "user").map((n) => n.text));
  $$("#queued .queued-msg").forEach((n) => { if (texts.has(n.dataset.text)) n.remove(); });
}

// ---- delegated clicks --------------------------------------------------------------------------------------------------------------
document.addEventListener("click", (e) => {
  const t = e.target;
  let b;
  if ((b = t.closest("[data-toggle-step]"))) { const id = b.dataset.toggleStep; ctx.ui.expandedSteps.has(id) ? ctx.ui.expandedSteps.delete(id) : ctx.ui.expandedSteps.add(id); doRender(); return; }
  if ((b = t.closest("[data-toggle-file]"))) { const id = b.dataset.toggleFile; ctx.ui.openFiles.has(id) ? ctx.ui.openFiles.delete(id) : ctx.ui.openFiles.add(id); doRender(); return; }
  if ((b = t.closest("[data-open-artifact]"))) { openArtifactInPanel(b.dataset.openArtifact); return; }
  if ((b = t.closest("[data-src-btn]"))) {
    const n = Number(b.dataset.srcBtn); ctx.ui.highlightSource = n; const c = ctx.state.cites.get(n);
    renderSide();
    if (c) {
      const note = $(`[data-msg="${c.firstNote}"]`);
      if (note) {
        citeReturn = b;
        note.scrollIntoView({ block: "center" });
        $$(".cite.hit").forEach((x) => x.classList.remove("hit"));
        $$(`[data-msg="${c.firstNote}"] .cite`).forEach((x) => { if ((x.dataset.cite || "").split(",").includes(String(n))) x.classList.add("hit"); });
      } else if (ctx.ui.revealed !== Infinity) { revealNew(); }
    }
    return;
  }
  if ((b = t.closest("[data-goto-approval]"))) { openSideIfClosed(); focusApproval(b.dataset.gotoApproval); return; }
  if ((b = t.closest('[data-act="goto-approval"]'))) { focusApproval(b.dataset.approval); return; }
  if ((b = t.closest("[data-strip]"))) { setTab(b.dataset.strip, false); setSheet("open", "half"); return; }
  if ((b = t.closest('[data-act="art-back"]'))) { ctx.ui.panelArtifact = null; doRender(); const r = $(`#panel-artifacts [data-open-artifact]`); if (r) r.focus({ preventScroll: true }); return; }
  if ((b = t.closest('[data-act="art-expand"]'))) { expandArtifact(); return; }
  if ((b = t.closest('[data-act="ov-close"]'))) { closeOverlay(); return; }
  if ((b = t.closest('[data-act="export-run"]'))) { exportRun(); return; }
  if ((b = t.closest('[data-act="export-request"]'))) { requestExport(b.dataset.artifact); return; }
  if ((b = t.closest('[data-act="step-stop"]'))) { ctx.post(`/goals/${ctx.goal}/steps/${b.dataset.step}/stop`, {}).then((r) => toast(r.ok ? "Stop requested." : (r.body.error || "Couldn't stop that."), !r.ok)); return; }
  if ((b = t.closest('[data-act="step-note"]'))) { ctx.ui.noteTo = ctx.ui.noteTo === b.dataset.step ? null : b.dataset.step; doRender(); const i = $("#panel-progress input"); if (i) i.focus(); return; }
  if ((b = t.closest("[data-action]")) && !b.matches("a[href]")) { e.preventDefault(); ctx.perform(b.dataset.action); return; }
  const rowb = t.closest(".gl-row"); void rowb;
});
document.addEventListener("submit", (e) => {
  const f = e.target.closest("[data-noteform]"); if (!f) return;
  e.preventDefault();
  const v = f.querySelector("input").value.trim(); if (!v) return;
  ctx.post(`/goals/${ctx.goal}/messages`, { text: v, to: f.dataset.noteform }).then((r) => { if (r.ok) { toast("Note sent."); ctx.ui.noteTo = null; doRender(); } else toast(r.body.error || "Couldn't send the note.", true); });
});
async function requestExport(id) {
  const r = await ctx.post(`/goals/${ctx.goal}/artifacts/${id}/export`, {});
  if (!r.ok) return toast(r.body.error || "Couldn't request the export.", true);
  toast("Export needs your approval. It is in the approval dock.");
  closeOverlay(); setTimeout(() => focusApproval(), 300);
}
function exportRun() {
  const blob = new Blob([JSON.stringify({ goal: ctx.goal, events: ctx.events }, null, 2)], { type: "application/json" });
  const a = el("a", "", "", { href: URL.createObjectURL(blob), download: `${ctx.goal}-run.json` }); document.body.appendChild(a); a.click(); a.remove();
  toast("Exported the event stream. Nothing left the box.");
}

// ---- actions used by keys and palette -----------------------------------------------------------------------------------------
ctx.actions = {
  "ws.new_goal": () => { location.href = "/workspace?new=1"; },
  "ws.focus_composer": () => { ta.focus(); },
  "ws.send": () => { $("#composer").requestSubmit(); },
  "ws.toggle_goals": () => toggleGoals(),
  "ws.toggle_side": () => toggleSide(),
  "ws.reveal_new": () => revealNew(),
  "ws.focus_sidebar": () => { openSideIfClosed(); const t = $(`#tab-${ctx.ui.tab}`); if (t) t.focus(); },
  "ws.export_run": () => exportRun(),
  "ws.restart_replay": () => restartReplay(),
  "ws.raise_cap": () => { const i = $("#cap-input"); if (i) i.focus(); else toast("This goal isn't paused at its cap."); },
  "ws.approval_focus": () => { if (!focusApproval()) toast("Nothing is waiting for you."); },
  "ws.stop": () => stopRun(),
  "ws.view_full": () => {
    const c = document.activeElement && document.activeElement.closest && document.activeElement.closest(".acard");
    if (c) { const a = ctx.state.approvals.get(c.dataset.approval); if (a) return fullView(a); }
    if (ctx.ui.panelArtifact || document.activeElement.closest("[data-art]")) { const id = document.activeElement.closest("[data-art]"); if (id) ctx.ui.panelArtifact = id.dataset.art; return expandArtifact(); }
  },
};
function stopRun() {
  const now = performance.now();
  if (now - ctx.ui.stopArmedAt < 1500) {
    ctx.ui.stopArmedAt = -Infinity;
    ctx.post(`/goals/${ctx.goal}/stop`, {}).then((r) => toast(r.ok ? "Run stopped. The sandbox was kept." : (r.body.error || "Couldn't stop the run."), !r.ok));
  } else { ctx.ui.stopArmedAt = now; toast("Press . again to stop the run."); }
}
async function restartReplay() {
  if (!ctx.boot.replay || !ctx.goal) return toast("Replay is off.");
  await ctx.post(`/goals/${ctx.goal}/replay`, { action: "restart", speed: Number(q.get("speed") ?? 4), hold: q.get("hold") !== "0" });
  location.reload();
}

// ---- render loop -----------------------------------------------------------------------------------------------------------------
let raf = 0;
let renders = 0;
function doRender() {
  raf = 0; renders++;
  renderChrome();
  renderTimeline();
  renderDock();
  if (!$("#panel-" + ctx.ui.tab).hidden) renderSide(); else renderSide();
  renderGoals();
  clearQueued();
}
ctx.render = () => { if (!raf) raf = requestAnimationFrame(doRender); };

// ---- boot ----------------------------------------------------------------------------------------------------------------------------
function applyLayout() {
  if (store("oc-ws:left") === "closed") ws.dataset.left = "closed";
  if (store("oc-ws:right") === "closed") ws.dataset.right = "closed";
  $("#btn-left").setAttribute("aria-expanded", String(ws.dataset.left !== "closed"));
  $("#btn-right").setAttribute("aria-expanded", String(ws.dataset.right !== "closed"));
  if (isPhone()) ws.dataset.sheet = "closed";
  const tab = ctx.persist("tab"); if (tab && ctx.boot.tabs.includes(tab)) ctx.ui.tab = tab;
  try { JSON.parse(ctx.persist("reviewed") || "[]").forEach((k) => ctx.ui.reviewed.add(k)); } catch (e) { /* ignore */ }
}

// Phones: with no goal open the goal list is the whole screen, so it is also the page's main landmark.
function syncScreen() {
  const list = isPhone() && !ctx.goal && q.get("new") !== "1";
  ws.dataset.screen = list ? "list" : "chat";
  const g = $("#goals-col");
  g.setAttribute("role", list ? "main" : "complementary");
  $("#gl-h1").hidden = !list;
  $("#main").setAttribute("role", list ? "presentation" : "main");
}

async function main() {
  applyLayout();
  installKeys();
  setTab(ctx.ui.tab, false);
  const notice = store("oc-ws:notice"); if (notice) { store("oc-ws:notice", null); toast(notice); }
  if (!ctx.goal) {
    const e = $("#empty-goal"); e.hidden = false;
    e.appendChild(el("h2", "empty-title", "Give the agent a goal"));
    e.appendChild(el("p", "muted", "It plans, works in a sandbox, and asks before anything leaves the box. Progress, sources, files, changes and cost appear on the right as it goes."));
    ws.dataset.noGoal = "1";
    if (q.get("new") === "1") ta.focus();
  } else {
    if (ctx.boot.replay) {
      const speed = Number(q.get("speed") ?? 4);
      await ctx.post(`/goals/${ctx.goal}/replay`, { action: q.get("restart") === "1" ? "restart" : "start", speed, hold: q.get("hold") !== "0" });
    }
    connect();
  }
  syncScreen();
  matchMedia("(max-width: 767px)").addEventListener("change", syncScreen);
  setBanner();
  await loadGoals();
  if (!ctx.goal && ctx.boot.replay) {
    const e = $("#empty-goal"); const t = el("div", "try"); t.appendChild(el("p", "caption muted", "Or open a recorded run:"));
    goals.forEach((g) => { const a = el("a", "", g.title, { href: "/workspace/" + g.id }); a.appendChild(el("small", "", g.replay.summary)); t.appendChild(a); });
    e.appendChild(t);
  }
  setInterval(() => { if (!document.hidden) loadGoals(); }, 4000);
  doRender();
}
main();

// Test hooks: a stable, normalised snapshot of the projected DOM for the determinism gate, and the folded summary.
window.__oc = {
  summary: () => summary(ctx.state),
  count: () => ctx.events.length,
  renders: () => renders,
  snapshot() {
    Object.keys(RENDER).forEach((k) => RENDER[k]($("#panel-" + k)));
    const clone = (sel) => { const n = $(sel).cloneNode(true); n.querySelectorAll("[data-sig],[data-key]").forEach((x) => { x.removeAttribute("data-sig"); }); n.querySelectorAll(".hit,[data-cur]").forEach((x) => x.classList.remove("hit")); return n.innerHTML; };
    return { timeline: clone("#timeline"), side: clone("#panels"), dock: clone("#dock"), chrome: $(".ws-title").innerText };
  },
};
