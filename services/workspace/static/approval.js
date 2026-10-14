// Approval card: one component used for the dock above the composer. Never a modal, never steals focus.
// Keys when the card has focus: a allow once, Shift+A allow for this goal, r deny (optional note), f full payload, Esc defer.
import { el, icon, money, ago } from "./util.js";
import { ctx } from "./ctx.js";
import { diffView } from "./viewers.js";

const RISK = { low: ["neutral", "neutral", "Low risk"], elevated: ["attention", "attention", "Elevated risk"], high: ["urgent", "attention", "High risk"] };
const KIND = { network: "Network request", push: "Push", email: "Email", spend: "Spend", export: "Export", plan: "Plan" };

export function chip(cls, ic, text) {
  const s = el("span", "st st-" + cls); s.appendChild(icon(ic)); s.appendChild(el("span", "", text)); return s;
}

export function payloadBlock(a, full) {
  const text = a.payload_preview || (typeof a.payload === "string" ? a.payload : JSON.stringify(a.payload, null, 2));
  if (/^(---|diff )/m.test(text) && /^@@ /m.test(text)) return diffView(text);
  const pre = el("pre", "payload mono" + (full ? " full" : ""), text, { tabindex: "0", "aria-label": "Exact payload" });
  return pre;
}

export function buildCard(a) {
  const high = a.risk === "high";
  const armed = ctx.ui.armed.get(a.id);
  const card = el("section", "acard risk-" + a.risk, null, { tabindex: "0", role: "group", "aria-label": "Approval needed: " + a.action, "data-approval": a.id, "data-fid": "card-" + a.id, "aria-keyshortcuts": "a Shift+A r f Escape" });
  const head = el("div", "acard-head");
  const [rc, ri, rt] = RISK[a.risk] || RISK.low;
  head.appendChild(chip(rc, ri, rt));
  head.appendChild(el("span", "acard-kind caption muted", KIND[a.kind] || a.kind));
  head.appendChild(el("span", "grow"));
  head.appendChild(el("span", "acard-cost num" + (a.cost_usd ? "" : " muted"), a.cost_usd ? "Costs " + money(a.cost_usd) : "No spend"));
  card.appendChild(head);
  card.appendChild(el("h2", "acard-title", a.action));
  if (a.destination) {
    const d = el("p", "acard-dest mono"); d.appendChild(icon("external")); d.appendChild(el("span", "", a.destination));
    d.title = "Leaves the box for " + a.destination;
    d.appendChild(el("span", "sr", " (leaves the box)"));
    card.appendChild(d);
  }
  const pv = el("div", "acard-payload");
  pv.appendChild(payloadBlock(a));
  const full = el("button", "btn ghost sm", "View full", { type: "button", "data-act": "full", "aria-keyshortcuts": "f", "data-fid": "full-" + a.id });
  full.prepend(icon("expand"));
  pv.appendChild(full);
  card.appendChild(pv);
  const info = el("dl", "acard-info");
  if (a.why) { info.appendChild(el("dt", "", "Why")); info.appendChild(el("dd", "", a.why)); }
  if (a.if_denied) { info.appendChild(el("dt", "", "If denied")); info.appendChild(el("dd", "", a.if_denied)); }
  info.appendChild(el("dt", "", "Bound to")); const hd = el("dd", "mono", "sha256 " + a.payload_hash.slice(0, 12)); hd.title = a.payload_hash; info.appendChild(hd);
  card.appendChild(info);
  const note = el("div", "acard-note", null, { hidden: "" });
  note.appendChild(el("label", "caption", "Note to the agent (optional)", { for: "dn-" + a.id }));
  note.appendChild(el("textarea", "note-input", null, { id: "dn-" + a.id, rows: "2", "data-fid": "dn-" + a.id }));
  card.appendChild(note);
  const acts = el("div", "acard-acts");
  const once = el("button", "btn " + (high ? (armed === "once" ? "primary armed" : "") : "primary"), armed === "once" ? "Press again to confirm" : "Allow once", { type: "button", "data-act": "once", "aria-keyshortcuts": "a", "data-fid": "once-" + a.id });
  once.appendChild(el("kbd", high ? "" : "on-accent", "a"));
  const goal = el("button", "btn" + (armed === "goal" ? " armed" : ""), armed === "goal" ? "Press again to confirm" : "Allow for this goal", { type: "button", "data-act": "goal", "aria-keyshortcuts": "Shift+A", "data-fid": "goal-" + a.id });
  goal.appendChild(el("kbd", "", "shift+a"));
  const deny = el("button", "btn", "Deny", { type: "button", "data-act": "deny", "aria-keyshortcuts": "r", "data-fid": "deny-" + a.id });
  deny.appendChild(el("kbd", "", "r"));
  acts.appendChild(once); acts.appendChild(goal); acts.appendChild(deny);
  card.appendChild(acts);
  const scope = el("p", "acard-scope caption muted");
  scope.textContent = a.scope_pattern ? "Allow for this goal grants: " + a.scope_pattern + ". Single use is the default." : "Allow once covers exactly this payload.";
  if (high) scope.textContent += " High risk: each allow needs a second press.";
  card.appendChild(scope);
  card.appendChild(el("p", "sr", "Press Escape to decide later. Escape never allows or denies.", { id: "acard-esc-" + a.id }));
  card.setAttribute("aria-describedby", "acard-esc-" + a.id);
  return card;
}

export function sigOf(a) {
  return [a.id, a.status, a.payload_hash, ctx.ui.armed.get(a.id) || "", ctx.ui.deferred.has(a.id) ? "d" : ""].join("|");
}

async function decide(a, choice, root) {
  const noteEl = root.querySelector("textarea");
  const body = { choice, hash: a.payload_hash, note: noteEl && !noteEl.closest("[hidden]") ? noteEl.value : "" };
  if (choice !== "deny" && a.risk === "high") body.confirmed = true;
  const hadFocus = root.contains(document.activeElement);
  root.dataset.busy = "1";
  try {
    const r = await ctx.post(`/goals/${ctx.goal}/approvals/${a.id}/decide`, body);
    if (r.ok) {
      ctx.ui.armed.delete(a.id);
      ctx.announce((choice === "deny" ? "Denied: " : "Allowed: ") + a.action);
      if (hadFocus) document.getElementById("composer-text").focus({ preventScroll: true });
    } else {
      ctx.ui.armed.delete(a.id);
      ctx.toast(r.body.error || "That didn't go through. Nothing was decided.", true);
      if (r.status === 409) refreshApprovals();
    }
  } catch (e) {
    ctx.toast("Couldn't reach the console. Nothing was decided.", true);
  }
  delete root.dataset.busy;
  ctx.render();
}

function refreshApprovals() { /* the stream carries the authoritative state; a sweep runs on reconnect */ }

// press: "once" | "goal" | "deny" | "deny-submit"
export function press(a, which, root) {
  if (root.dataset.busy) return;
  if (which === "deny") {
    const note = root.querySelector(".acard-note");
    if (note.hidden) { note.hidden = false; note.querySelector("textarea").focus(); const d = root.querySelector('[data-act="deny"]'); d.firstChild.textContent = "Send denial"; d.dataset.act = "deny-submit"; return; }
  }
  if (which === "deny-submit" || which === "deny") return decide(a, "deny", root);
  if (a.risk === "high" && ctx.ui.armed.get(a.id) !== which) {
    ctx.ui.armed.set(a.id, which);
    ctx.render();
    const b = document.querySelector(`#dock [data-act="${which}"]`); if (b) b.focus({ preventScroll: true });
    ctx.announce("High risk. Press again to confirm.");
    return;
  }
  return decide(a, which, root);
}

export function defer(a) {
  ctx.ui.deferred.add(a.id);
  ctx.ui.armed.delete(a.id);
  ctx.render();
  const c = document.getElementById("composer-text"); if (c) c.focus({ preventScroll: true });
  ctx.announce("Deferred. It stays in the Outbox and nothing was decided.");
}

export function wireCard(root, a) {
  root.addEventListener("click", (e) => {
    const b = e.target.closest("[data-act]"); if (!b) return;
    const act = b.dataset.act;
    if (act === "full") return fullView(a);
    press(a, act, root);
  });
  root.addEventListener("keydown", (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const t = e.target;
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); if (t.tagName === "TEXTAREA" && !t.value) { /* fall through to defer */ } defer(a); return; }
    if (t.tagName === "TEXTAREA") return;
    if (e.key === "a") { e.preventDefault(); press(a, "once", root); }
    else if (e.key === "A") { e.preventDefault(); press(a, "goal", root); }
    else if (e.key === "r") { e.preventDefault(); press(a, "deny", root); }
    else if (e.key === "f") { e.preventDefault(); fullView(a); }
  });
}

export function fullView(a) {
  const ov = document.getElementById("overlay");
  ov.textContent = "";
  ov.hidden = false;
  ov.dataset.opener = "";
  const bar = el("div", "ov-bar");
  bar.appendChild(el("h2", "ov-title", "Payload: " + a.action));
  const close = el("button", "btn", "Close", { type: "button", "data-act": "ov-close" }); close.appendChild(el("kbd", "", "esc"));
  bar.appendChild(close);
  ov.appendChild(bar);
  const body = el("div", "ov-body"); body.appendChild(payloadBlock(a, true));
  ov.appendChild(body);
  ctx.openOverlay && ctx.openOverlay(close);
}

export function buildCapCard() {
  const s = ctx.state, spent = s.cost.spent;
  const card = el("section", "acard cap", null, { tabindex: "0", role: "group", "aria-label": "Paused at the spend cap", "data-fid": "capcard" });
  const head = el("div", "acard-head"); head.appendChild(chip("attention", "attention", "Paused at cap")); card.appendChild(head);
  card.appendChild(el("h2", "acard-title", `The ${money(s.cost.cap)} cap is reached`));
  card.appendChild(el("p", "caption muted", `${money(spent)} spent. The run is paused, not cancelled: the sandbox and everything gathered so far are kept.`));
  const row = el("form", "cap-form", null, { "data-capform": "" });
  row.appendChild(el("label", "caption", "New cap (USD)", { for: "cap-input" }));
  const sug = Math.max(Math.ceil(spent * 2.5 * 2) / 2, spent + 0.5);
  row.appendChild(el("input", "input", null, { id: "cap-input", type: "number", min: String(Math.ceil(spent * 100) / 100 + 0.01), step: "any", value: sug.toFixed(2), inputmode: "decimal", "data-fid": "cap-input" }));
  row.appendChild(el("button", "btn primary", "Raise cap and resume", { type: "submit", "data-fid": "cap-go" }));
  card.appendChild(row);
  card.appendChild(el("p", "caption muted", "Raising the cap is the only way to continue. Stop the run from the title bar to end it here."));
  return card;
}
