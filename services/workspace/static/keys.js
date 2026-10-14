// Keyboard map, command palette and `?` overlay. All of it is generated from the same action registry the
// console uses (#oc-actions); nothing here invents a shortcut. Keys can be rebound from the `?` overlay.
import { $, $$, el, store, typing } from "./util.js";
import { ctx } from "./ctx.js";

const ACTIONS = JSON.parse($("#oc-actions").textContent);
const byId = {}; ACTIONS.forEach((a) => { byId[a.id] = a; });
const OVERRIDE_KEY = "oc-ws:keys";
let overrides = {}; try { overrides = JSON.parse(store(OVERRIDE_KEY) || "{}"); } catch (e) { overrides = {}; }
const keyOf = (a) => (overrides[a.id] != null ? overrides[a.id] : a.keys);

export function toast(msg, error) {
  const host = $(error ? "#alerts" : "#toasts");
  const t = el("div", "toast" + (error ? " error" : ""), msg);
  host.appendChild(t);
  setTimeout(() => t.remove(), error ? 20000 : 6000);
}
export function announce(msg) {
  const a = $("#announce"); a.textContent = ""; setTimeout(() => { a.textContent = msg; }, 30);
}

// ---- layers: palette and help (one at a time) and the expanded-artifact overlay ----------------------------------------------
let layer = null, overlayOpener = null;
function openLayer(build, label) {
  closeLayer();
  const opener = document.activeElement;
  const wrap = el("div", "layer"), dlg = el("div", "dialog", null, { role: "dialog", "aria-modal": "true", "aria-label": label });
  wrap.appendChild(dlg);
  wrap.addEventListener("mousedown", (e) => { if (e.target === wrap) closeLayer(); });
  $("#layers").appendChild(wrap);
  layer = { wrap, opener };
  build(dlg);
  return dlg;
}
export function closeLayer() {
  if (!layer) return false;
  layer.wrap.remove(); const o = layer.opener; if (layer.onClose) layer.onClose(); layer = null;
  if (o && o.focus && document.contains(o)) o.focus();
  return true;
}
// While an expanded view is open the rest of the page is inert, so it is also out of the accessibility tree.
function setInert(on) {
  ["goals-col", "side", "banner", "foot"].forEach((id) => { const n = document.getElementById(id); if (n) n.inert = on; });
  $$(".ws-title, .tl-wrap").forEach((n) => { n.inert = on; });
}
export function openOverlay(focusEl) {
  overlayOpener = document.activeElement;
  document.body.classList.add("ov-open");
  setInert(true);
  if (focusEl) focusEl.focus();
}
export function closeOverlay() {
  const ov = $("#overlay");
  if (ov.hidden) return false;
  ov.hidden = true; ov.textContent = ""; document.body.classList.remove("ov-open");
  setInert(false);
  if (overlayOpener && document.contains(overlayOpener)) overlayOpener.focus({ preventScroll: true });
  overlayOpener = null;
  return true;
}

// ---- palette -----------------------------------------------------------------------------------------------------------------------
function dynamicItems() {
  const s = ctx.state, out = [];
  document.querySelectorAll("#goal-list [data-goal]").forEach((a) => out.push({ id: "goal:" + a.dataset.goal, label: a.querySelector(".gl-title").textContent, group: "Goals", keys: "", href: a.getAttribute("href"), note: "" }));
  s.artifacts.forEach((a) => out.push({ id: "art:" + a.id, label: a.name, group: "Artifacts", keys: "", note: "v" + a.version }));
  s.sources.forEach((x) => out.push({ id: "src:" + x.id, label: x.title, group: "Sources", keys: "", note: x.domain || "" }));
  return out;
}
function openPalette() {
  if (layer && layer.wrap.querySelector("[role=combobox]")) { closeLayer(); return; }
  openLayer((dlg) => {
    const input = el("input", "", null, { type: "text", placeholder: "Search goals, artifacts, sources and commands", role: "combobox", "aria-label": "Command palette", "aria-expanded": "true", "aria-controls": "pal-list", autocomplete: "off" });
    const ul = el("ul", "", null, { id: "pal-list", role: "listbox", "aria-label": "Results", tabindex: "-1" });
    dlg.appendChild(input); dlg.appendChild(ul);
    let sel = 0, shown = [];
    const all = () => ACTIONS.filter((a) => a.id !== "palette").concat(dynamicItems());
    function render() {
      const words = input.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
      shown = all().filter((a) => { const h = (a.label + " " + a.group + " " + keyOf(a) + " " + (a.note || "")).toLowerCase(); return words.every((w) => h.includes(w)); }).slice(0, 200);
      ul.textContent = "";
      if (!shown.length) { const m = el("li", "empty-msg muted", "No match. Try a command, goal or file name.", { role: "presentation" }); ul.appendChild(m); }
      shown.forEach((a, i) => {
        const li = el("li", "", null, { id: "pal-" + i, role: "option", "aria-selected": i === sel ? "true" : "false" });
        li.appendChild(el("span", "grp", a.group)); li.appendChild(el("span", "lbl", a.label));
        if (a.note) li.appendChild(el("span", "nt", a.note));
        if (keyOf(a)) li.appendChild(el("kbd", "", keyOf(a)));
        li.addEventListener("mousedown", (ev) => { ev.preventDefault(); run(a); });
        ul.appendChild(li);
      });
      input.setAttribute("aria-activedescendant", shown.length ? "pal-" + sel : "");
      const cur = $("#pal-" + sel); if (cur) cur.scrollIntoView({ block: "nearest" });
    }
    function run(a) { const o = layer && layer.opener; closeLayer(); perform(a.id, a, o); }
    input.addEventListener("input", () => { sel = 0; render(); });
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") { e.preventDefault(); sel = Math.min(sel + 1, shown.length - 1); render(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); sel = Math.max(sel - 1, 0); render(); }
      else if (e.key === "Enter") { e.preventDefault(); if (shown[sel]) run(shown[sel]); }
      else if (e.key === "Tab") e.preventDefault();
    });
    render(); input.focus();
  }, "Command palette");
}

// ---- ? overlay with rebinding ----------------------------------------------------------------------------------------------------------
function openHelp() {
  if (layer && layer.wrap.querySelector("[data-help]")) { closeLayer(); return; }
  openLayer((dlg) => {
    dlg.dataset.help = "1";
    const input = el("input", "", null, { type: "text", placeholder: "Filter shortcuts", "aria-label": "Filter keyboard shortcuts" });
    const body = el("div", "help-body", null, { tabindex: "0", "aria-label": "Shortcuts" });
    dlg.appendChild(input); dlg.appendChild(body);
    const order = []; ACTIONS.forEach((a) => { if (!order.includes(a.group)) order.push(a.group); });
    let rebinding = null;
    function render() {
      const q = input.value.trim().toLowerCase(); body.textContent = ""; let any = false;
      order.forEach((g) => {
        const rows = ACTIONS.filter((a) => a.group === g && keyOf(a) && (a.label + " " + keyOf(a)).toLowerCase().includes(q));
        if (!rows.length) return; any = true;
        body.appendChild(el("h3", "", g));
        const ul = el("ul");
        rows.forEach((a) => {
          const li = el("li", "static"); li.appendChild(el("span", "lbl", a.label));
          li.appendChild(el("kbd", "", rebinding === a.id ? "press a key" : keyOf(a)));
          if (/^(shift\+)?[a-z/.\[\]]$/i.test(a.keys) && a.scope === "workspace" && a.group !== "Approvals" && a.group !== "Sidebar") {
            const r = el("button", "btn ghost sm", rebinding === a.id ? "Cancel" : "Rebind", { type: "button", "aria-label": "Rebind " + a.label });
            r.addEventListener("click", () => { rebinding = rebinding === a.id ? null : a.id; render(); });
            li.appendChild(r);
          }
          ul.appendChild(li);
        });
        body.appendChild(ul);
      });
      if (!any) body.appendChild(el("p", "empty-msg muted", "No shortcut matches."));
      if (Object.keys(overrides).length) { const r = el("button", "btn ghost sm", "Reset all bindings", { type: "button" }); r.addEventListener("click", () => { overrides = {}; store(OVERRIDE_KEY, null); rebuildKeymap(); render(); toast("Bindings reset."); }); body.appendChild(r); }
    }
    input.addEventListener("input", render);
    const onKey = (e) => {
      if (!rebinding) return;
      if (["Shift", "Control", "Alt", "Meta"].includes(e.key)) return;
      e.preventDefault(); e.stopPropagation();
      if (e.key === "Escape") { rebinding = null; render(); return; }
      const k = e.shiftKey && e.key.length === 1 ? "shift+" + e.key.toLowerCase() : e.key;
      if (e.key.length !== 1) { toast("Use a single character key.", true); return; }
      const clash = ACTIONS.find((a) => a.id !== rebinding && keyOf(a) === k && (a.scope === "workspace" || a.scope === "global"));
      if (clash) { toast(`${k} is already used by "${clash.label}".`, true); return; }
      overrides[rebinding] = k; store(OVERRIDE_KEY, JSON.stringify(overrides)); rebinding = null; rebuildKeymap(); render(); toast("Rebound to " + k);
    };
    // on document, in the capture phase: re-rendering the list drops focus from the button that was clicked
    document.addEventListener("keydown", onKey, true);
    layer.onClose = () => document.removeEventListener("keydown", onKey, true);
    render(); input.focus();
  }, "Keyboard shortcuts");
}

// ---- theme -------------------------------------------------------------------------------------------------------------------------------
function cycleTheme() {
  const cur = document.documentElement.getAttribute("data-theme") || "system";
  const next = { system: "dark", dark: "light", light: "system" }[cur];
  if (next === "system") document.documentElement.removeAttribute("data-theme"); else document.documentElement.setAttribute("data-theme", next);
  store("oc-theme", next); toast("Theme: " + next);
}

// ---- dispatch --------------------------------------------------------------------------------------------------------------------------------
export function perform(id, a) {
  if (id === "palette") return openPalette();
  if (id === "help") return openHelp();
  if (id === "theme") return cycleTheme();
  if (id === "close") return escape();
  if (id.startsWith("goal:")) { location.href = a.href; return; }
  if (id.startsWith("art:")) { ctx.openArtifactInPanel(id.slice(4)); return; }
  if (id.startsWith("src:")) { const x = ctx.state.sources.get(id.slice(4)); if (x) ctx.mdCtx.onCite([x.n], document.body); return; }
  if (id.startsWith("ws.tab.")) { ctx.setTab(id.slice(7), true); return; }
  if (ctx.actions && ctx.actions[id]) return ctx.actions[id]();
  if (id === "row.down") return moveRow(1);
  if (id === "row.up") return moveRow(-1);
  if (id === "row.open") { const f = document.activeElement; if (f && f.click && f.matches(".row-btn, .gl-row")) f.click(); return; }
  if (id === "ws.select") return selectRow();
  if (id === "ws.msg_next") return stepMsg(1);
  if (id === "ws.msg_prev") return stepMsg(-1);
  if (a && a.href) location.href = a.href;
}
ctx.perform = (id) => perform(id, byId[id]);

// ---- lists: j/k/Enter/x --------------------------------------------------------------------------------------------------------------------
function activeList() {
  const f = document.activeElement;
  const host = f && f.closest && f.closest(".rows, #goal-list");
  if (host) return host;
  return null;
}
function moveRow(d) {
  let host = activeList();
  if (!host) {
    if (document.activeElement && document.activeElement.closest("#side")) host = $("#panel-" + ctx.ui.tab + " .rows");
    else host = $("#goal-list");
  }
  if (!host) return;
  const items = $$(".row-btn, .gl-row", host).filter((n) => n.offsetParent !== null);
  if (!items.length) return;
  const i = items.indexOf(document.activeElement);
  const n = i < 0 ? (d > 0 ? 0 : items.length - 1) : Math.max(0, Math.min(items.length - 1, i + d));
  items[n].focus(); items[n].scrollIntoView({ block: "nearest" });
}
function selectRow() {
  const f = document.activeElement; if (!f || !f.matches(".row-btn, .gl-row")) return;
  const li = f.closest("li") || f; const on = li.classList.toggle("sel");
  if (f.matches(".row-btn")) f.setAttribute("aria-pressed", String(on));
  const key = (li.dataset.art && "art:" + li.dataset.art) || (f.dataset.goal && "goal:" + f.dataset.goal) || (li.dataset.src && "src:" + li.dataset.src) || (li.dataset.out && "out:" + li.dataset.out);
  if (key) on ? ctx.ui.selected.add(key) : ctx.ui.selected.delete(key);
  toast(ctx.ui.selected.size ? ctx.ui.selected.size + " selected. Esc clears." : "Selection cleared.");
}

function stepMsg(d) {
  const msgs = $$("[data-msg]"); if (!msgs.length) return;
  const cur = document.activeElement && document.activeElement.closest && document.activeElement.closest("[data-msg]");
  let i = cur ? msgs.indexOf(cur) : (d > 0 ? -1 : msgs.length);
  i = Math.max(0, Math.min(msgs.length - 1, i + d));
  msgs[i].focus(); msgs[i].scrollIntoView({ block: "nearest" });
}

// ---- escape: closes the topmost layer and returns focus ---------------------------------------------------------------------------------------
export function escape() {
  if (closeLayer()) return true;
  if (closeOverlay()) return true;
  if (ctx.citeReturnFocus && !$("#cite-pop").hidden === false && ctx.citeReturnFocus()) return true;
  if (ctx.ui.panelArtifact && document.activeElement.closest("#panel-artifacts")) { ctx.ui.panelArtifact = null; ctx.render(); return true; }
  if (ctx.sheetOpen && ctx.sheetOpen()) { ctx.setSheet("closed"); return true; }
  if (ctx.ui.selected.size) { ctx.ui.selected.clear(); $$(".sel").forEach((n) => n.classList.remove("sel")); return true; }
  if (ctx.ui.highlightSource != null) { ctx.ui.highlightSource = null; ctx.render(); if (ctx.citeReturnFocus()) return true; }
  return false;
}

// ---- keymap ------------------------------------------------------------------------------------------------------------------------------------
let keymap = {}, gMap = {};
function rebuildKeymap() {
  keymap = {}; gMap = {};
  ACTIONS.forEach((a) => {
    const k = keyOf(a); if (!k) return;
    let m;
    if ((m = /^g (\S)$/.exec(k))) { gMap[m[1]] = a; return; }
    if (a.scope !== "workspace" && a.scope !== "global") return;
    if (/^shift\+(.)$/.test(k)) keymap[k.slice(6).toUpperCase()] = a;
    else if (k.length === 1) keymap[k] = a;
  });
  ["j", "k"].forEach((c) => { const id = c === "j" ? "row.down" : "row.up"; keymap[c] = byId[id]; });
}
let gArmed = 0;
export function installKeys() {
  rebuildKeymap();
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openPalette(); return; }
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); ctx.actions["ws.send"](); return; }
    if (e.key === "Escape") {
      const inCard = e.target.closest && e.target.closest(".acard");
      if (inCard) return; // the card owns Esc: it defers, it never decides
      const ta = e.target; if (ta && ta.id === "composer-text") { ta.blur(); e.preventDefault(); return; }
      if (escape()) e.preventDefault();
      return;
    }
    if (layer) return;
    if (typing(e.target) || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.target.closest && e.target.closest(".acard") && ["a", "A", "r", "f"].includes(e.key)) return; // handled by the card
    if (gArmed && performance.now() - gArmed < 1500) {
      gArmed = 0;
      const g = gMap[e.key]; if (g) { e.preventDefault(); perform(g.id, g); }
      return;
    }
    // tab keys 1-7 only while the sidebar has focus
    if (/^[1-7]$/.test(e.key) && e.target.closest && e.target.closest("#side")) { e.preventDefault(); ctx.setTab(ctx.boot.tabs[Number(e.key) - 1], true); return; }
    if (e.key === "g") { gArmed = performance.now(); return; }
    if (e.key === "?") { e.preventDefault(); openHelp(); return; }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (activeList() && !e.target.closest(".dialog")) { e.preventDefault(); moveRow(e.key === "ArrowDown" ? 1 : -1); }
      return;
    }
    const a = keymap[e.key];
    if (!a || /^ws\.(allow|deny)/.test(a.id) || /^ws\.tab\./.test(a.id)) return; // card-owned or sidebar-focus-only
    if (a.id === "ws.view_full" && !(e.target.closest && (e.target.closest("[data-art]") || e.target.closest("#panel-artifacts")))) return;
    e.preventDefault(); perform(a.id, a);
  });
}
