// Timeline: the conversation. Entries are projections of notes, steps, approvals, artifacts, errors and run changes,
// in the order they first appeared. Entries are never reordered; updates replace an entry in place.
import { el, icon, dur, money, absTime } from "./util.js";
import { ctx } from "./ctx.js";
import { list, stepTools, elapsed } from "./projection.js";
import { render as markdown } from "./markdown.js";
import { KIND_LABEL } from "./viewers.js";

export const STEP_ICON = { pending: "circle", running: "dot", done: "check", failed: "x", skipped: "dash" };
export const STEP_WORD = { pending: "Pending", running: "Running", done: "Done", failed: "Failed", skipped: "Skipped" };
const STEP_CLS = { pending: "neutral", running: "healthy", done: "healthy", failed: "urgent", skipped: "neutral" };
const KIND_ICON = { markdown: "doc", html: "globe", image: "doc", svg: "doc", pdf: "doc", csv: "doc", code: "file", text: "file", diff: "diff", mermaid: "doc", archive: "file", office: "doc", audio: "doc", video: "doc" };

export function statusGlyph(status) {
  const s = el("span", "st st-" + (STEP_CLS[status] || "neutral"));
  s.appendChild(icon(STEP_ICON[status] || "circle"));
  s.appendChild(el("span", "sr", STEP_WORD[status] || status));
  return s;
}

function relStart(ts) {
  const s = ctx.state;
  return s.run.startedTs == null ? "" : "+" + dur(ts - s.run.startedTs);
}

function noteCtx(n) {
  const quotes = new Map((n.cites || []).map((c) => [c.n, c.quote || ""]));
  return Object.assign({}, ctx.mdCtx, { quote: (k) => quotes.get(k) || "" });
}

function noteEntry(id) {
  const n = ctx.state.notes.get(id);
  if (n.role === "system" && n.kind !== "message") {
    const lvl = n.kind === "threshold" && /reached|paused/i.test(n.text) ? "urgent" : "attention";
    return { key: "note:" + id, sig: n.text, build() {
      const d = el("div", "evt evt-" + lvl, null, { "data-evt": "" });
      d.appendChild(icon(lvl === "urgent" ? "urgent" : "attention")); d.appendChild(el("span", "", n.text));
      return d;
    } };
  }
  return { key: "note:" + id, sig: [n.text, n.role].join("\u0001"), build() {
    const a = el("article", "msg msg-" + n.role + (n.kind === "final" ? " final" : ""), null, { "data-msg": id, tabindex: "-1", "aria-label": n.role === "user" ? "You" : (n.actor && n.actor.name) || "Agent" });
    const head = el("header", "msg-head");
    if (n.role === "agent") { const g = el("span", "glint"); g.appendChild(icon("glint")); g.title = "Agent " + ((n.actor && n.actor.name) || "agent") + (n.actor && n.actor.session ? ", session " + n.actor.session : "") + ", " + absTime(n.ts); g.setAttribute("role", "img"); g.setAttribute("aria-label", g.title); head.appendChild(g); }
    head.appendChild(el("span", "msg-who", n.role === "user" ? "You" : n.role === "system" ? "Console" : (n.actor && n.actor.name) || "Agent"));
    const t = el("time", "msg-time caption muted", relStart(n.ts)); t.title = absTime(n.ts); head.appendChild(t);
    a.appendChild(head);
    const body = el("div", "md msg-body");
    body.appendChild(markdown(n.text, noteCtx(n)));
    a.appendChild(body);
    return a;
  } };
}

function stepEntry(id) {
  const s = ctx.state, st = s.steps.get(id), tools = stepTools(s, id);
  const open = ctx.ui.expandedSteps.has(id);
  const last = tools[tools.length - 1];
  const t = st.startedTs == null ? 0 : (st.endedTs != null ? st.endedTs : s.lastTs) - st.startedTs;
  const sig = [st.status, st.title, tools.length, last ? last.status + last.summary : "", open, Math.round(t)].join("|");
  return { key: "step:" + id, sig, build() {
    const w = el("section", "act act-" + st.status, null, { "data-step": id });
    const b = el("button", "act-head", null, { type: "button", "aria-expanded": open ? "true" : "false", "data-fid": "step-" + id, "data-toggle-step": id });
    b.appendChild(icon(open ? "chev-down" : "chev-right", "chev"));
    b.appendChild(statusGlyph(st.status));
    b.appendChild(el("span", "act-title", st.title));
    if (tools.length) b.appendChild(el("span", "act-count caption muted", tools.length + (tools.length === 1 ? " action" : " actions")));
    b.appendChild(el("span", "grow"));
    if (st.startedTs != null) b.appendChild(el("span", "act-time caption muted num", dur(t)));
    w.appendChild(b);
    if (open) {
      const ul = el("ul", "act-tools");
      tools.forEach((c) => {
        const li = el("li", "tool tool-" + c.status);
        li.appendChild(statusGlyph(c.status === "ok" ? "done" : c.status === "error" ? "failed" : "running"));
        li.appendChild(el("span", "tool-name mono", c.name));
        li.appendChild(el("span", "tool-sum", c.summary || ""));
        if (c.destination) li.appendChild(el("span", "tool-dest mono muted", c.destination));
        if (c.duration_ms != null) li.appendChild(el("span", "caption muted num", c.duration_ms >= 1000 ? (c.duration_ms / 1000).toFixed(1) + "s" : c.duration_ms + "ms"));
        ul.appendChild(li);
      });
      w.appendChild(ul);
    } else if (st.status === "running" && last) {
      w.appendChild(el("p", "act-live caption muted", last.summary || last.name));
    }
    return w;
  } };
}

function approvalEntry(id) {
  const a = ctx.state.approvals.get(id);
  const sig = [a.status, a.decision || "", a.decided_by || "", ctx.ui.deferred.has(id)].join("|");
  return { key: "approval:" + id, sig, build() {
    const d = el("div", "ap-line ap-" + a.status, null, { "data-ap-line": id });
    if (a.status === "pending") {
      d.appendChild(icon("attention"));
      d.appendChild(el("span", "", (ctx.ui.deferred.has(id) ? "Deferred: " : "Waiting for your decision: ") + a.action));
      const b = el("button", "btn ghost sm", "Review", { type: "button", "data-act": "goto-approval", "data-approval": id, "data-fid": "goto-" + id });
      d.appendChild(b);
    } else {
      const ok = a.status === "approved";
      d.appendChild(icon(ok ? "ok" : a.status === "denied" ? "urgent" : "neutral"));
      const word = ok ? (a.decision === "goal" ? "Allowed for this goal" : "Allowed once") : a.status === "denied" ? "Denied" : a.status === "superseded" ? "Replaced by a changed request" : "Expired";
      const sp = el("span", "", word + (a.decided_by ? " by " + a.decided_by : "") + ": " + a.action);
      d.appendChild(sp);
      if (a.destination) d.appendChild(el("span", "mono muted caption", a.destination));
      if (a.decidedTs && a.askedTs) d.appendChild(el("span", "caption muted num", "after " + dur(a.decidedTs - a.askedTs)));
    }
    return d;
  } };
}

function artifactEntry(id) {
  const a = ctx.state.artifacts.get(id);
  const sig = [a.version, a.name, a.unreviewed, ctx.ui.panelArtifact === id].join("|");
  return { key: "artifact:" + id, sig, build() {
    const b = el("button", "chip-art", null, { type: "button", "data-open-artifact": id, "data-fid": "art-chip-" + id });
    b.appendChild(icon(KIND_ICON[a.kind] || "doc"));
    b.appendChild(el("span", "chip-art-name", a.name));
    b.appendChild(el("span", "chip-art-kind caption muted", KIND_LABEL[a.kind] || a.kind));
    b.appendChild(el("span", "ver", "v" + a.version));
    if (a.unreviewed) { b.appendChild(el("span", "dot-unreviewed", null, { "aria-hidden": "true" })); b.appendChild(el("span", "sr", "Unreviewed")); }
    const w = el("div", "art-entry"); w.appendChild(b); return w;
  } };
}

function errorEntry(id) {
  const e = ctx.state.errors.get(id);
  return { key: "error:" + id, sig: e.message + (e.hint || ""), build() {
    const d = el("div", "evt evt-urgent", null, { "data-evt": "" });
    d.appendChild(icon("urgent"));
    const t = el("span", ""); t.appendChild(el("strong", "", e.message));
    if (e.hint) t.appendChild(el("span", "muted", " " + e.hint));
    d.appendChild(t);
    return d;
  } };
}

function runEntry(seq) {
  const r = ctx.state.runEvents.get(seq);
  const cur = String(ctx.state.lastRunSeq) === seq;
  const s = ctx.state;
  const sig = [r.status, r.reason, cur, Math.round(elapsed(s))].join("|");
  return { key: "run:" + seq, sig, build() {
    const cls = r.status === "done" ? "healthy" : r.status === "paused" ? "attention" : r.status === "running" ? "healthy" : r.status === "failed" ? "urgent" : "neutral";
    const d = el("div", "run-line run-" + r.status, null, { "data-run-line": r.status });
    const ic = r.status === "done" ? "ok" : r.status === "paused" ? "pause" : r.status === "running" ? "dot" : r.status === "failed" ? "urgent" : "stop";
    const g = el("span", "st st-" + cls); g.appendChild(icon(ic)); d.appendChild(g);
    const word = r.status === "done" ? "Run completed" : r.status === "paused" ? (r.reason === "cap" ? "Paused at the spend cap" : "Paused") : r.status === "running" ? "Resumed" + (r.capUsd ? " with a " + money(r.capUsd) + " cap" : "") : r.status === "failed" ? "Run failed" : "Run stopped";
    d.appendChild(el("strong", "", word));
    if (r.summary) d.appendChild(el("span", "muted", r.summary));
    if (r.status === "done" || r.status === "failed" || r.status === "cancelled") d.appendChild(el("span", "caption muted num", "after " + dur(elapsed(s))));
    return d;
  } };
}

export function timelineItems() {
  const s = ctx.state;
  const items = [];
  // The goal itself opens the conversation even before the user note arrives.
  for (const t of s.timeline) {
    if (t.kind === "note") items.push(noteEntry(t.ref));
    else if (t.kind === "step") items.push(stepEntry(t.ref));
    else if (t.kind === "approval") items.push(approvalEntry(t.ref));
    else if (t.kind === "artifact") items.push(artifactEntry(t.ref));
    else if (t.kind === "error") items.push(errorEntry(t.ref));
    else if (t.kind === "run") items.push(runEntry(t.ref));
  }
  return items;
}

export { list };
