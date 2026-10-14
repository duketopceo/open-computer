// Small DOM and formatting helpers. No dependencies, no build step.
export const $ = (s, r) => (r || document).querySelector(s);
export const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));

export function el(tag, cls, text, attrs) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  if (attrs) for (const k in attrs) if (attrs[k] != null && attrs[k] !== false) n.setAttribute(k, attrs[k] === true ? "" : attrs[k]);
  return n;
}

const SVGNS = "http://www.w3.org/2000/svg";
export function icon(name, cls) {
  const s = document.createElementNS(SVGNS, "svg");
  s.setAttribute("class", "ic" + (cls ? " " + cls : ""));
  s.setAttribute("aria-hidden", "true");
  s.setAttribute("focusable", "false");
  const u = document.createElementNS(SVGNS, "use");
  u.setAttribute("href", "#i-" + name);
  s.appendChild(u);
  return s;
}

export function store(k, v) {
  try {
    if (v === undefined) return localStorage.getItem(k);
    if (v === null) localStorage.removeItem(k); else localStorage.setItem(k, v);
  } catch (e) { /* storage blocked: the UI works without it */ }
  return null;
}

export const typing = (t) => !!t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable);

export function money(x) {
  x = Number(x || 0);
  if (x === 0) return "$0.00";
  if (x >= 0.01) return "$" + x.toFixed(2);
  return "$" + x.toFixed(4);
}

export function dur(s) {
  s = Math.max(0, Math.round(s));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  if (h) return h + "h " + m + "m";
  return m ? m + "m " + String(sec) + "s" : sec + "s";
}

// Relative to a reference time that comes from the stream, never the wall clock, so replays render identically.
export function ago(ts, ref) {
  const d = Math.max(0, ref - ts);
  if (d < 5) return "just now";
  if (d < 60) return Math.round(d) + "s ago";
  if (d < 3600) return Math.round(d / 60) + "m ago";
  return Math.round(d / 3600) + "h ago";
}

export function absTime(ts) {
  return new Date(ts * 1000).toISOString().replace("T", " ").replace(/\.\d+Z$/, " UTC");
}

export function bytes(n) {
  if (n < 1024) return n + " B";
  if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
  return (n / 1048576).toFixed(1) + " MB";
}

export const num = (n) => Number(n || 0).toLocaleString("en-US");

// Keyed reconciler. Keeps existing nodes whose signature is unchanged, swaps changed ones in place, and
// restores focus if the focused element lived inside a node that was replaced. Nodes already in the right
// place are never moved, so the row under the cursor never shifts. `parent` must hold only keyed children.
export function reconcile(parent, items) {
  const have = new Map();
  for (const c of Array.from(parent.children)) have.set(c.dataset.key, c);
  const nodes = [];
  for (const it of items) {
    let node = have.get(it.key);
    if (!node || node.dataset.sig !== it.sig) {
      const fresh = it.build();
      fresh.dataset.key = it.key;
      fresh.dataset.sig = it.sig;
      if (node) {
        const active = document.activeElement;
        const fid = active && node.contains(active) ? active.getAttribute("data-fid") : null;
        node.replaceWith(fresh);
        if (fid) {
          const t = fresh.querySelector('[data-fid="' + CSS.escape(fid) + '"]');
          if (t) t.focus({ preventScroll: true });
        }
      }
      node = fresh;
    }
    nodes.push(node);
  }
  const want = new Set(nodes);
  for (const c of Array.from(parent.children)) if (!want.has(c)) c.remove();
  let ref = parent.firstElementChild;
  for (const n of nodes) {
    if (n === ref) ref = ref.nextElementSibling;
    else parent.insertBefore(n, ref);
  }
}
