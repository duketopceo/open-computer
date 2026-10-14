// Built-in renderer for the Mermaid flowchart subset the agent emits (flowchart/graph, LR/RL/TD/TB; nodes
// A[text] A(text) A{text} A((text)); edges -->, ---, -.-> with optional |label|). No library, no CDN. Anything
// else returns null and the viewer falls back to showing the source, so nothing is ever silently dropped.
const NS = "http://www.w3.org/2000/svg";
const s = (tag, attrs, text) => { const n = document.createElementNS(NS, tag); for (const k in attrs || {}) n.setAttribute(k, attrs[k]); if (text != null) n.textContent = text; return n; };

export function parse(src) {
  const lines = src.split(/\n/).map((l) => l.replace(/%%.*$/, "").trim()).filter(Boolean);
  const head = /^(flowchart|graph)\s+(LR|RL|TD|TB|BT)$/.exec(lines[0] || "");
  if (!head) return null;
  const nodes = new Map(), edges = [];
  const NODE = /^([A-Za-z_][\w-]*)(?:\[\(?([^\]]+?)\)?\]|\(\(([^)]+)\)\)|\(([^)]+)\)|\{([^}]+)\})?/;
  function node(str) {
    const m = NODE.exec(str.trim());
    if (!m || m[0].length !== str.trim().length) return null;
    const id = m[1], label = m[2] || m[3] || m[4] || m[5];
    const shape = m[5] ? "diamond" : m[3] ? "round" : m[4] ? "pill" : "rect";
    const cur = nodes.get(id) || { id, label: id, shape: "rect" };
    if (label) { cur.label = label; cur.shape = shape; }
    nodes.set(id, cur);
    return id;
  }
  for (const l of lines.slice(1)) {
    const parts = l.split(/\s*(-->|---|-\.->)\s*(?:\|([^|]*)\|\s*)?/);
    if (parts.length === 1) { if (!node(l)) return null; continue; }
    const ids = [];
    const labels = [];
    for (let i = 0; i < parts.length; i += 3) {
      const id = node(parts[i]);
      if (!id) return null;
      ids.push(id);
      if (i + 2 < parts.length + 0) labels.push(parts[i + 2] || "");
    }
    for (let k = 0; k + 1 < ids.length; k++) edges.push({ from: ids[k], to: ids[k + 1], label: labels[k] || "" });
  }
  if (!nodes.size) return null;
  return { dir: head[2] === "TB" ? "TD" : head[2], nodes: Array.from(nodes.values()), edges };
}

function layout(g) {
  const out = new Map(g.nodes.map((n) => [n.id, []]));
  g.edges.forEach((e) => out.get(e.from).push(e.to));
  const layer = new Map();
  const state = new Map();
  const back = new Set();
  const dfs = (id) => {
    state.set(id, 1);
    for (const t of out.get(id)) {
      if (state.get(t) === 1) back.add(id + ">" + t);
      else if (!state.has(t)) dfs(t);
    }
    state.set(id, 2);
  };
  g.nodes.forEach((n) => { if (!state.has(n.id)) dfs(n.id); });
  const order = g.nodes.map((n) => n.id);
  order.forEach((id) => layer.set(id, 0));
  for (let pass = 0; pass < g.nodes.length; pass++)
    for (const e of g.edges) if (!back.has(e.from + ">" + e.to)) layer.set(e.to, Math.max(layer.get(e.to), layer.get(e.from) + 1));
  const cols = new Map();
  order.forEach((id) => { const l = layer.get(id); if (!cols.has(l)) cols.set(l, []); cols.get(l).push(id); });
  return { layer, cols };
}

export function render(src) {
  const g = parse(src);
  if (!g) return null;
  const { layer, cols } = layout(g);
  const horizontal = g.dir === "LR" || g.dir === "RL";
  const W = 148, H = 44, GX = 56, GY = 28, PAD = 16;
  const pos = new Map();
  const nLayers = Math.max(...cols.keys()) + 1;
  const maxPer = Math.max(...Array.from(cols.values()).map((c) => c.length));
  g.nodes.forEach((n) => {
    const l = layer.get(n.id), col = cols.get(l), i = col.indexOf(n.id);
    const a = l * ((horizontal ? W : H) + (horizontal ? GX : GX));
    const b = (i + (maxPer - col.length) / 2) * ((horizontal ? H : W) + GY);
    pos.set(n.id, horizontal ? { x: PAD + (g.dir === "RL" ? (nLayers - 1) * (W + GX) - a : a), y: PAD + b } : { x: PAD + b, y: PAD + a });
  });
  const width = PAD * 2 + (horizontal ? nLayers * W + (nLayers - 1) * GX : maxPer * W + (maxPer - 1) * GY);
  const height = PAD * 2 + (horizontal ? maxPer * H + (maxPer - 1) * GY : nLayers * H + (nLayers - 1) * GX);
  const svg = s("svg", { viewBox: `0 0 ${width} ${height}`, width, height, class: "mmd", role: "img", "aria-label": "Diagram with " + g.nodes.length + " steps and " + g.edges.length + " connections" });
  const defs = s("defs");
  const mk = s("marker", { id: "mmd-arrow", viewBox: "0 0 10 10", refX: "9", refY: "5", markerWidth: "7", markerHeight: "7", orient: "auto-start-reverse" });
  mk.appendChild(s("path", { d: "M0 0L10 5L0 10z", class: "mmd-arrow" }));
  defs.appendChild(mk); svg.appendChild(defs);
  g.edges.forEach((e) => {
    const a = pos.get(e.from), b = pos.get(e.to);
    const x1 = a.x + (horizontal ? (b.x >= a.x ? W : 0) : W / 2), y1 = a.y + (horizontal ? H / 2 : (b.y >= a.y ? H : 0));
    const x2 = b.x + (horizontal ? (b.x >= a.x ? 0 : W) : W / 2), y2 = b.y + (horizontal ? H / 2 : (b.y >= a.y ? 0 : H));
    const dx = horizontal ? (x2 - x1) / 2 : 0, dy = horizontal ? 0 : (y2 - y1) / 2;
    svg.appendChild(s("path", { d: `M${x1} ${y1} C${x1 + dx} ${y1 + dy} ${x2 - dx} ${y2 - dy} ${x2} ${y2}`, class: "mmd-edge", "marker-end": "url(#mmd-arrow)", fill: "none" }));
    if (e.label) svg.appendChild(s("text", { x: (x1 + x2) / 2, y: (y1 + y2) / 2 - 6, class: "mmd-elabel", "text-anchor": "middle" }, e.label));
  });
  g.nodes.forEach((n) => {
    const p = pos.get(n.id);
    const grp = s("g", { class: "mmd-node mmd-" + n.shape });
    if (n.shape === "diamond") grp.appendChild(s("path", { d: `M${p.x + W / 2} ${p.y - 4} L${p.x + W + 4} ${p.y + H / 2} L${p.x + W / 2} ${p.y + H + 4} L${p.x - 4} ${p.y + H / 2}z` }));
    else grp.appendChild(s("rect", { x: p.x, y: p.y, width: W, height: H, rx: n.shape === "rect" ? 6 : H / 2 }));
    const label = n.label.length > 22 ? n.label.slice(0, 21) + "…" : n.label;
    const t = s("text", { x: p.x + W / 2, y: p.y + H / 2 + 4, "text-anchor": "middle" }, label);
    t.appendChild(s("title", {}, n.label));
    grp.appendChild(t);
    svg.appendChild(grp);
  });
  return svg;
}
