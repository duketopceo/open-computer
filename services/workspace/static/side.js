// Sidebar: seven tabs, each a pure projection of the state. Lists append in first-seen order and update in
// place, so nothing under the cursor moves while events stream in.
import { el, icon, money, dur, num, reconcile, ago, absTime, bytes } from "./util.js";
import { ctx } from "./ctx.js";
import { list, pendingApprovals, stepTools, spentPct, contextPct } from "./projection.js";
import { STEP_ICON, STEP_WORD, statusGlyph } from "./timeline.js";
import { KIND_LABEL, KIND_ORDER, openViewer, diffView } from "./viewers.js";
import { chip } from "./approval.js";

const empty = (title, body) => { const d = el("div", "empty sm"); d.appendChild(el("p", "empty-title", title)); d.appendChild(el("p", "muted caption", body)); return d; };
const sectionHead = (t, extra) => { const h = el("h3", "sec-head", t); if (extra) h.appendChild(el("span", "sec-extra caption muted num", extra)); return h; };

function listHost(panel, cls, label) {
  let ul = panel.querySelector(":scope > ul." + cls);
  if (!ul) { ul = el("ul", "rows " + cls, null, { "aria-label": label }); panel.appendChild(ul); }
  return ul;
}
function head(panel, text, extra) {
  let h = panel.querySelector(":scope > .sec-head.top");
  if (!h) { h = el("h2", "sec-head top"); panel.prepend(h); }
  h.textContent = text;
  if (extra) { const x = el("span", "sec-extra caption muted num", extra); h.appendChild(x); }
}

// ---- 1 Progress -------------------------------------------------------------------------------------------
function progress(panel) {
  const s = ctx.state, steps = list(s.steps).filter((x) => !x.synthetic);
  const done = steps.filter((x) => x.status === "done").length;
  head(panel, "Plan", steps.length ? `${done} of ${steps.length} done` : "");
  let empt = panel.querySelector(":scope > .empty"); if (empt) empt.remove();
  const ul = listHost(panel, "plan", "Plan");
  if (!steps.length) { ul.hidden = true; panel.appendChild(empty("No plan yet", "The agent's steps appear here as it plans.")); return; }
  ul.hidden = false;
  reconcile(ul, steps.map((st) => {
    const tools = stepTools(s, st.id), last = tools[tools.length - 1];
    const t = st.startedTs == null ? null : (st.endedTs != null ? st.endedTs : s.lastTs) - st.startedTs;
    const sub = st.kind === "sandbox" || st.kind === "subagent";
    const sig = [st.status, st.title, Math.round(t || 0), last ? last.summary : "", ctx.ui.noteTo === st.id, st.agent || ""].join("|");
    return { key: st.id, sig, build() {
      const li = el("li", "plan-row plan-" + st.status, null, { "data-step-row": st.id });
      const row = el("div", "plan-main");
      row.appendChild(statusGlyph(st.status).cloneNode(true));
      row.lastChild.firstChild.setAttribute("class", "ic" + (st.status === "running" ? " live" : ""));
      row.appendChild(el("span", "plan-title" + (st.status === "running" ? " shimmer" : ""), st.title));
      row.appendChild(el("span", "plan-state sr", STEP_WORD[st.status]));
      if (t != null) row.appendChild(el("span", "plan-time caption muted num", dur(t)));
      li.appendChild(row);
      if (st.status === "running" && last) li.appendChild(el("p", "plan-live caption muted", last.summary || last.name));
      if (sub) {
        const ac = el("div", "plan-actor");
        ac.appendChild(icon(st.kind === "sandbox" ? "cube" : "glint")); ac.appendChild(el("span", "mono caption", st.agent || st.kind));
        ac.appendChild(el("span", "grow"));
        const live = st.status === "running";
        const stop = el("button", "btn ghost sm", "Stop", { type: "button", "data-act": "step-stop", "data-step": st.id, "data-fid": "stop-" + st.id, "aria-label": "Stop " + (st.agent || st.title) });
        if (!live) stop.disabled = true;
        const note = el("button", "btn ghost sm", "Send note", { type: "button", "data-act": "step-note", "data-step": st.id, "data-fid": "note-" + st.id, "aria-label": "Send note to " + (st.agent || st.title) });
        ac.appendChild(stop); ac.appendChild(note);
        li.appendChild(ac);
        if (ctx.ui.noteTo === st.id) {
          const f = el("form", "plan-note", null, { "data-noteform": st.id });
          f.appendChild(el("label", "sr", "Note to " + (st.agent || st.title), { for: "sn-" + st.id }));
          f.appendChild(el("input", "input", null, { id: "sn-" + st.id, type: "text", placeholder: "Note to " + (st.agent || "the agent"), "data-fid": "sn-" + st.id }));
          f.appendChild(el("button", "btn primary sm", "Send", { type: "submit" }));
          li.appendChild(f);
        }
      }
      return li;
    } };
  }));
}

// ---- 2 Sources -----------------------------------------------------------------------------------------------
export function tile(domain) {
  const t = el("span", "tile", (domain || "?").replace(/^www\./, "").charAt(0).toUpperCase(), { "aria-hidden": "true" });
  return t;
}
function sources(panel) {
  const s = ctx.state, srcs = list(s.sources);
  const unv = srcs.filter((x) => !x.fetched).length;
  head(panel, "Sources", srcs.length ? `${srcs.length} sources${unv ? ", " + unv + " not verified" : ""}` : "");
  let empt = panel.querySelector(":scope > .empty"); if (empt) empt.remove();
  const ul = listHost(panel, "src", "Sources");
  if (!srcs.length) { ul.hidden = true; panel.appendChild(empty("No sources yet", "Pages the agent reads and cites are listed here, once each.")); return; }
  ul.hidden = false;
  reconcile(ul, srcs.map((x) => {
    const c = s.cites.get(x.n);
    const hl = ctx.ui.highlightSource === x.n;
    const sig = [x.title, x.fetched, c ? c.count : 0, hl, x.fetched_at || 0, Math.round(s.lastTs / 30)].join("|");
    return { key: x.id, sig, build() {
      const li = el("li", "src-row" + (hl ? " hit" : ""), null, { "data-src": String(x.n) });
      const b = el("button", "row-btn", null, { type: "button", "data-fid": "src-" + x.id, "data-src-btn": String(x.n), "aria-describedby": "srcd-" + x.id });
      b.appendChild(tile(x.domain));
      const col = el("span", "row-col");
      col.appendChild(el("span", "row-title", x.title));
      const meta = el("span", "row-meta caption muted");
      meta.appendChild(el("span", "mono", x.domain || ""));
      meta.appendChild(el("span", "", c ? " \u00b7 cited " + c.count + "\u00d7" : " \u00b7 not cited yet"));
      if (x.fetched && x.fetched_at) meta.appendChild(el("span", "", " · fetched " + ago(x.fetched_at, s.lastTs)));
      col.appendChild(meta);
      if (!x.fetched) { const nv = chip("attention", "attention", "Not verified"); nv.classList.add("nv"); col.appendChild(nv); }
      b.appendChild(col);
      b.appendChild(el("span", "ref-n num", "[" + x.n + "]"));
      li.appendChild(b);
      li.appendChild(el("p", "sr", (x.snippet || "No excerpt.") + (x.fetched ? "" : " Cited without being fetched."), { id: "srcd-" + x.id }));
      return li;
    } };
  }));
}

// ---- 3 Artifacts ---------------------------------------------------------------------------------------------
function artifacts(panel) {
  const s = ctx.state, arts = list(s.artifacts);
  const open = ctx.ui.panelArtifact && s.artifacts.get(ctx.ui.panelArtifact);
  let host = panel.querySelector(":scope > .art-view");
  if (open) {
    panel.querySelectorAll(":scope > :not(.art-view)").forEach((n) => n.hidden = true);
    const sig = open.id + ":" + open.version;
    if (!host || host.dataset.sig !== sig) {
      if (host) host.remove();
      host = el("div", "art-view"); host.dataset.sig = sig;
      const bar = el("div", "art-view-bar");
      const back = el("button", "btn ghost sm", "Artifacts", { type: "button", "data-act": "art-back", "data-fid": "art-back" }); back.prepend(icon("chev-left"));
      bar.appendChild(back); bar.appendChild(el("h2", "art-view-name mono", open.name));
      const ex = el("button", "btn ghost sm", "Expand", { type: "button", "data-act": "art-expand", "data-fid": "art-expand", "aria-keyshortcuts": "f" }); ex.prepend(icon("expand")); ex.appendChild(el("kbd", "", "f"));
      bar.appendChild(el("span", "grow")); bar.appendChild(ex);
      host.appendChild(bar);
      host.appendChild(openViewer(open, ctx.viewerCtx()));
      panel.appendChild(host);
    }
    return;
  }
  if (host) host.remove();
  panel.querySelectorAll(":scope > [hidden]").forEach((n) => { if (!n.classList.contains("empty-keep")) n.hidden = false; });
  const unrev = arts.filter((a) => a.unreviewed).length;
  head(panel, "Artifacts", arts.length ? `${arts.length} files${unrev ? ", " + unrev + " unreviewed" : ""}` : "");
  let empt = panel.querySelector(":scope > .empty"); if (empt) empt.remove();
  const wrap = panel.querySelector(":scope > .art-groups") || panel.appendChild(el("div", "art-groups"));
  if (!arts.length) { wrap.hidden = true; panel.appendChild(empty("Nothing made yet", "Files the goal produces show up here, with versions.")); return; }
  wrap.hidden = false;
  // one <ul> per type, in a fixed type order; each reconciled by artifact id
  for (const kind of KIND_ORDER) {
    const rows = arts.filter((a) => a.kind === kind);
    let sec = wrap.querySelector(`:scope > section[data-kind="${kind}"]`);
    if (!rows.length) { if (sec) sec.remove(); continue; }
    if (!sec) {
      sec = el("section", "art-group", null, { "data-kind": kind });
      sec.appendChild(el("h3", "grp-head", KIND_LABEL[kind]));
      sec.appendChild(el("ul", "rows art", null, { "aria-label": KIND_LABEL[kind] + " artifacts" }));
      const after = KIND_ORDER.slice(KIND_ORDER.indexOf(kind) + 1).map((k) => wrap.querySelector(`:scope > section[data-kind="${k}"]`)).find(Boolean);
      wrap.insertBefore(sec, after || null);
    }
    reconcile(sec.querySelector("ul"), rows.map((a) => ({ key: a.id, sig: [a.version, a.unreviewed, ctx.ui.selected.has("art:" + a.id), a.name].join("|"), build() {
      const li = el("li", "art-row" + (ctx.ui.selected.has("art:" + a.id) ? " sel" : ""), null, { "data-art": a.id });
      const b = el("button", "row-btn", null, { type: "button", "data-fid": "art-" + a.id, "data-open-artifact": a.id, "aria-pressed": ctx.ui.selected.has("art:" + a.id) ? "true" : "false" });
      b.appendChild(icon(a.kind === "diff" ? "diff" : a.kind === "code" || a.kind === "text" ? "file" : "doc"));
      const col = el("span", "row-col"); col.appendChild(el("span", "row-title", a.name));
      const meta = el("span", "row-meta caption muted");
      meta.textContent = [a.summary, a.size != null ? bytes(a.size) : ""].filter(Boolean).join(" · ");
      col.appendChild(meta); b.appendChild(col);
      if (a.export && a.export.requires_approval) { const l = icon("lock"); l.setAttribute("class", "ic muted"); b.appendChild(l); b.appendChild(el("span", "sr", "Export needs approval")); }
      b.appendChild(el("span", "ver", "v" + a.version));
      if (a.unreviewed) { b.appendChild(el("span", "dot-unreviewed", null, { "aria-hidden": "true" })); b.appendChild(el("span", "sr", "Unreviewed")); }
      li.appendChild(b); return li;
    } })));
  }
}

// ---- 4 Changes ------------------------------------------------------------------------------------------------
function changes(panel) {
  const s = ctx.state, ch = list(s.changes);
  const add = ch.reduce((a, c) => a + c.additions, 0), del = ch.reduce((a, c) => a + c.deletions, 0);
  let hunks = 0; for (const c of ch) hunks += (c.diff || "").split("\n").filter((l) => l.startsWith("@@ ")).length;
  const rev = Array.from(ctx.ui.reviewed).filter((k) => ch.some((c) => k.startsWith(c.id + ":"))).length;
  head(panel, "Changes", ch.length ? `${ch.length} files, +${add} −${del}` : "");
  let empt = panel.querySelector(":scope > .empty"); if (empt) empt.remove();
  let sum = panel.querySelector(":scope > .chg-sum");
  const ul = listHost(panel, "chg", "Changed files");
  if (!ch.length) { ul.hidden = true; if (sum) sum.remove(); panel.appendChild(empty("No changes", "Edits to the sandbox appear here as a diff against where it started. This is what would leave the box.")); return; }
  ul.hidden = false;
  if (!sum) { sum = el("p", "chg-sum caption muted"); panel.insertBefore(sum, ul); }
  sum.textContent = `${rev} of ${hunks} hunks reviewed. Nothing here has left the box.`;
  reconcile(ul, ch.map((c) => {
    const open = ctx.ui.openFiles.has(c.id);
    const mine = Array.from(ctx.ui.reviewed).filter((k) => k.startsWith(c.id + ":")).join(",");
    return { key: c.id, sig: [c.additions, c.deletions, open, mine, c.diff ? c.diff.length : 0].join("|"), build() {
      const li = el("li", "chg-row", null, { "data-change": c.id });
      const b = el("button", "row-btn", null, { type: "button", "aria-expanded": open ? "true" : "false", "data-fid": "chg-" + c.id, "data-toggle-file": c.id });
      b.appendChild(icon(open ? "chev-down" : "chev-right", "chev"));
      b.appendChild(el("span", "row-title mono", c.path));
      b.appendChild(el("span", "op caption muted", c.op));
      b.appendChild(el("span", "plus num", "+" + c.additions));
      b.appendChild(el("span", "minus num", "−" + c.deletions));
      li.appendChild(b);
      if (open && c.diff) {
        const reviewed = new Set(Array.from(ctx.ui.reviewed).filter((k) => k.startsWith(c.id + ":")).map((k) => k.slice(c.id.length + 1)));
        li.appendChild(diffView(c.diff, { review: reviewed, idPrefix: "", noPath: true, onReview: (hid, on) => { const k = c.id + ":" + hid; if (on) ctx.ui.reviewed.add(k); else ctx.ui.reviewed.delete(k); ctx.persist("reviewed", JSON.stringify(Array.from(ctx.ui.reviewed))); ctx.render(); } }));
      }
      return li;
    } };
  }));
}

// ---- 5 Outbox -------------------------------------------------------------------------------------------------
function outbox(panel) {
  const s = ctx.state, aps = list(s.approvals).slice().reverse();
  const pend = pendingApprovals(s).length;
  head(panel, "Outbox", aps.length ? `${pend} pending of ${aps.length}` : "");
  let empt = panel.querySelector(":scope > .empty"); if (empt) empt.remove();
  const ul = listHost(panel, "out", "Things that would leave the box");
  const hosts = new Map();
  for (const t of list(s.tools)) if (t.destination && /^(web|git)\./.test(t.name)) hosts.set(t.destination, (hosts.get(t.destination) || 0) + 1);
  let eg = panel.querySelector(":scope > .egress");
  if (!aps.length && !hosts.size) { ul.hidden = true; if (eg) eg.remove(); panel.appendChild(empty("Nothing has left the box", "Network calls, pushes, emails and spend that need your decision are listed here with who decided and when.")); return; }
  ul.hidden = false;
  reconcile(ul, aps.map((a) => ({ key: a.id, sig: [a.status, a.decision, a.decided_by, ctx.ui.deferred.has(a.id)].join("|"), build() {
    const li = el("li", "out-row out-" + a.status, null, { "data-out": a.id });
    const b = el("button", "row-btn", null, { type: "button", "data-fid": "out-" + a.id, "data-goto-approval": a.id });
    const cls = a.status === "pending" ? "attention" : a.status === "approved" ? "healthy" : a.status === "denied" ? "urgent" : "neutral";
    const ic = a.status === "pending" ? "attention" : a.status === "approved" ? "ok" : a.status === "denied" ? "urgent" : "neutral";
    const g = el("span", "st st-" + cls); g.appendChild(icon(ic)); b.appendChild(g);
    const col = el("span", "row-col");
    col.appendChild(el("span", "row-title", a.action));
    const word = a.status === "pending" ? (ctx.ui.deferred.has(a.id) ? "Pending, deferred" : "Pending") : a.status === "approved" ? (a.decision === "goal" ? "Approved for this goal" : "Approved once") : a.status === "denied" ? "Denied" : a.status[0].toUpperCase() + a.status.slice(1);
    col.appendChild(el("span", "row-meta caption muted", word + (a.decided_by ? " by " + a.decided_by : "") + (a.decidedTs ? " · " + absTime(a.decidedTs).slice(11) : "") + (a.destination ? " · " + a.destination : "")));
    b.appendChild(col); li.appendChild(b); return li;
  } })));
  if (hosts.size) {
    if (!eg) { eg = el("section", "egress"); panel.appendChild(eg); }
    eg.textContent = "";
    eg.appendChild(sectionHead("Contacted", hosts.size + " destinations"));
    const u2 = el("ul", "rows plain", null, { "aria-label": "Destinations contacted" });
    hosts.forEach((n, h) => { const li = el("li", "kv-row"); li.appendChild(el("span", "mono", h)); li.appendChild(el("span", "muted num", n + (n === 1 ? " call" : " calls"))); u2.appendChild(li); });
    eg.appendChild(u2);
  }
}

// ---- 6 Box ------------------------------------------------------------------------------------------------------
function box(panel) {
  const s = ctx.state, b = s.run.box;
  head(panel, "Box", b ? b.template : "");
  panel.querySelectorAll(":scope > :not(.sec-head.top)").forEach((n) => n.remove());
  if (!b) { panel.appendChild(empty("No sandbox yet", "The sandbox's limits, network allow-list and live terminal appear once the run starts.")); return; }
  const kv = el("dl", "kv-list");
  [["Template", b.template], ["Time to live", b.ttl_s != null ? dur(b.ttl_s) : ""], ["CPU", b.cpu != null ? b.cpu + " cores" : ""], ["Memory", b.mem_mb != null ? num(b.mem_mb) + " MB" : ""], ["Disk", b.disk_mb != null ? num(b.disk_mb) + " MB" : ""]]
    .filter((r) => r[1]).forEach(([k, v]) => { kv.appendChild(el("dt", "", k)); kv.appendChild(el("dd", "mono", v)); });
  panel.appendChild(sectionHead("Limits")); panel.appendChild(kv);
  panel.appendChild(sectionHead("Network allow-list", (b.network_allow || []).length + " hosts"));
  const ul = el("ul", "rows plain", null, { "aria-label": "Allowed network destinations" });
  (b.network_allow || []).forEach((h) => { const li = el("li", "kv-row"); li.appendChild(icon("globe")); li.appendChild(el("span", "mono", h)); ul.appendChild(li); });
  if (!(b.network_allow || []).length) ul.appendChild(el("li", "kv-row muted", "No outbound network."));
  panel.appendChild(ul);
  const lines = [];
  for (const t of list(s.tools)) if (t.output_tail && t.output_tail.length) { lines.push("$ " + (t.summary || t.name)); lines.push(...t.output_tail); }
  panel.appendChild(sectionHead("Terminal"));
  const pre = el("pre", "term mono", lines.slice(-24).join("\n") || "No terminal output yet.", { tabindex: "0", "aria-label": "Terminal tail" });
  panel.appendChild(pre);
  panel.appendChild(el("p", "caption muted", "Browser frames: none in this run."));
}

// ---- 7 Cost -------------------------------------------------------------------------------------------------------
function cost(panel) {
  const s = ctx.state, c = s.cost;
  head(panel, "Cost", c.cap ? `${money(c.spent)} of ${money(c.cap)}` : "");
  panel.querySelectorAll(":scope > :not(.sec-head.top)").forEach((n) => n.remove());
  if (!c.cap && !c.spent) { panel.appendChild(empty("No spend yet", "Spend against the cap, tokens and context use appear once the agent starts working.")); return; }
  const pct = spentPct(s);
  const bar = el("progress", "bar" + (pct >= 1 ? " full" : pct >= 0.8 ? " warn" : ""), null, { max: "100", value: String(Math.min(100, Math.round(pct * 100))), "aria-label": "Spend against the cap" });
  panel.appendChild(bar);
  panel.appendChild(el("p", "cost-line", `${money(c.spent)} spent of ${money(c.cap)} cap · ${money(Math.max(0, c.cap - c.spent))} left`));
  if (pct >= 0.8) { const w = chip(pct >= 1 ? "urgent" : "attention", pct >= 1 ? "urgent" : "attention", pct >= 1 ? "Cap reached" : "Over 80% of the cap"); w.classList.add("cost-flag"); panel.appendChild(w); }
  panel.appendChild(sectionHead("Tokens"));
  const kv = el("dl", "kv-list");
  [["In", c.tokens.in], ["Out", c.tokens.out], ["Cached", c.tokens.cached]].forEach(([k, v]) => { kv.appendChild(el("dt", "", k)); kv.appendChild(el("dd", "num mono", num(v))); });
  panel.appendChild(kv);
  panel.appendChild(sectionHead("By model"));
  const ml = el("ul", "rows models", null, { "aria-label": "Spend by model" });
  c.byModel.forEach((m) => {
    const li = el("li", "model-row");
    const top = el("div", "model-top"); top.appendChild(el("span", "mono model-name", m.model)); top.appendChild(el("span", "num", money(m.usd))); li.appendChild(top);
    li.appendChild(el("span", "caption muted num", `${num(m.in)} in \u00b7 ${num(m.out)} out \u00b7 ${num(m.cached)} cached`));
    ml.appendChild(li);
  });
  panel.appendChild(ml);
  panel.appendChild(sectionHead("Context window", Math.round(contextPct(s) * 100) + "% used"));
  panel.appendChild(el("progress", "bar", null, { max: "100", value: String(Math.round(contextPct(s) * 100)), "aria-label": "Context window used" }));
  panel.appendChild(el("p", "caption muted num", `${num(c.context.used)} of ${num(c.context.window)} tokens`));
  const ex = el("button", "btn", "Export run", { type: "button", "data-act": "export-run", "data-fid": "export-run" }); ex.prepend(icon("external"));
  panel.appendChild(el("div", "cost-actions")).appendChild(ex);
}

export const RENDER = { progress, sources, artifacts, changes, outbox, box, cost };
export function renderSide() {
  const t = ctx.ui.tab;
  const panel = document.getElementById("panel-" + t);
  RENDER[t](panel);
  // badges are always current, whichever tab is open
  const s = ctx.state, pend = pendingApprovals(s).length;
  const unrev = list(s.artifacts).filter((a) => a.unreviewed).length;
  const badges = { progress: "", sources: s.sources.size || "", artifacts: s.artifacts.size || "", changes: s.changes.size || "", outbox: pend || (s.approvals.size ? "" : ""), box: "", cost: "" };
  for (const k in badges) {
    const b = document.querySelector(`[data-badge="${k}"]`);
    if (!b) continue;
    const v = badges[k];
    b.hidden = !v;
    b.textContent = v ? String(v) : "";
    b.classList.toggle("attn", k === "outbox" && pend > 0);
    b.setAttribute("aria-label", k === "outbox" ? pend + " pending" : k === "artifacts" && unrev ? v + " artifacts, " + unrev + " unreviewed" : v + " items");
  }
}
export { STEP_ICON, diffView };
