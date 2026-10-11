// A small, safe Markdown renderer. It builds DOM nodes with textContent only: no innerHTML, so no markup in
// the source can become active content. Supports headings, paragraphs, lists, tables, quotes, fences, rules,
// inline code, bold, italic, http(s) links, citation chips [1] and file citations [path:line].
import { el, icon } from "./util.js";

const INLINE = /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\*[^*\s][^*\n]*\*)|(\[[^\]\n]+\]\(https?:\/\/[^)\s]+\))|((?:\[\d+\])+)|(\[[A-Za-z0-9_./-]+:\d+\])/g;

function ranges(ns) {
  const out = [];
  for (const n of ns) {
    const last = out[out.length - 1];
    if (last && n === last[last.length - 1] + 1) last.push(n); else out.push([n]);
  }
  return out;
}

export function citeChip(ns, ctx) {
  const label = ns.length > 1 ? ns[0] + "–" + ns[ns.length - 1] : String(ns[0]);
  const b = el("button", "cite", label, { type: "button", "data-cite": ns.join(","), "aria-label": "Source " + label });
  b.dataset.fid = "cite-" + ns.join("-");
  if (ctx && ctx.onCite) {
    b.addEventListener("click", () => ctx.onCite(ns, b));
    b.addEventListener("mouseenter", () => ctx.showCite && ctx.showCite(ns, b));
    b.addEventListener("focus", () => ctx.showCite && ctx.showCite(ns, b));
    b.addEventListener("mouseleave", () => ctx.hideCite && ctx.hideCite());
    b.addEventListener("blur", () => ctx.hideCite && ctx.hideCite());
  }
  return b;
}

export function inline(text, ctx, parent) {
  const host = parent || document.createDocumentFragment();
  let i = 0, m;
  INLINE.lastIndex = 0;
  while ((m = INLINE.exec(text))) {
    if (m.index > i) host.appendChild(document.createTextNode(text.slice(i, m.index)));
    i = m.index + m[0].length;
    if (m[1]) host.appendChild(el("code", "", m[1].slice(1, -1)));
    else if (m[2]) host.appendChild(el("strong", "", m[2].slice(2, -2)));
    else if (m[3]) host.appendChild(el("em", "", m[3].slice(1, -1)));
    else if (m[4]) {
      const mm = /^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)$/.exec(m[4]);
      const a = el("a", "ext", mm[1], { href: mm[2], target: "_blank", rel: "noopener noreferrer", title: "Opens " + mm[2] });
      host.appendChild(a);
    } else if (m[5]) {
      if (ctx && ctx.cites === false) { host.appendChild(document.createTextNode(m[5])); continue; }
      const ns = (m[5].match(/\d+/g) || []).map(Number);
      for (const r of ranges(ns)) host.appendChild(citeChip(r, ctx));
    } else if (m[6]) {
      const ref = m[6].slice(1, -1);
      const b = el("button", "cite file", ref, { type: "button", "data-file": ref, "aria-label": "File " + ref });
      if (ctx && ctx.onFile) b.addEventListener("click", () => ctx.onFile(ref, b));
      host.appendChild(b);
    }
  }
  if (i < text.length) host.appendChild(document.createTextNode(text.slice(i)));
  return host;
}

export function render(src, ctx) {
  const root = document.createDocumentFragment();
  const lines = String(src).replace(/\r\n?/g, "\n").split("\n");
  let i = 0;
  const isTableSep = (l) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(l || "");
  const cells = (l) => l.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
  while (i < lines.length) {
    const l = lines[i];
    if (!l.trim()) { i++; continue; }
    let m;
    if ((m = /^```(\w*)\s*$/.exec(l))) {
      const buf = []; i++;
      while (i < lines.length && !/^```\s*$/.test(lines[i])) buf.push(lines[i++]);
      i++;
      const pre = el("pre", "md-code mono", null, { tabindex: "0", "aria-label": "Code" + (m[1] ? " (" + m[1] + ")" : "") });
      pre.appendChild(el("code", "", buf.join("\n")));
      root.appendChild(pre);
    } else if ((m = /^(#{1,4})\s+(.*)$/.exec(l))) {
      const h = el("h" + Math.min(m[1].length + 1, 5), "md-h");
      inline(m[2], ctx, h); root.appendChild(h); i++;
    } else if (/^\s*(-{3,}|\*{3,})\s*$/.test(l)) { root.appendChild(el("hr", "md-hr")); i++; }
    else if (/^\s*>/.test(l)) {
      const buf = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) buf.push(lines[i++].replace(/^\s*>\s?/, ""));
      const q = el("blockquote", "md-quote"); q.appendChild(render(buf.join("\n"), ctx)); root.appendChild(q);
    } else if (l.includes("|") && isTableSep(lines[i + 1])) {
      const head = cells(l); i += 2;
      const t = el("table", "md-table");
      const tr = el("tr");
      head.forEach((c) => { const th = el("th", "", null, { scope: "col" }); inline(c, ctx, th); tr.appendChild(th); });
      t.appendChild(el("thead")).appendChild(tr);
      const tb = el("tbody");
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) {
        const r = el("tr");
        cells(lines[i++]).forEach((c) => { const td = el("td"); inline(c, ctx, td); r.appendChild(td); });
        tb.appendChild(r);
      }
      t.appendChild(tb);
      const wrap = el("div", "md-table-wrap", null, { tabindex: "0", role: "region", "aria-label": "Table" });
      wrap.appendChild(t); root.appendChild(wrap);
    } else if (/^\s*([-*]|\d+\.)\s+/.test(l)) {
      const ordered = /^\s*\d+\./.test(l);
      const ul = el(ordered ? "ol" : "ul", "md-list");
      while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])) {
        const li = el("li"); inline(lines[i].replace(/^\s*([-*]|\d+\.)\s+/, ""), ctx, li); ul.appendChild(li); i++;
      }
      root.appendChild(ul);
    } else {
      const buf = [l]; i++;
      while (i < lines.length && lines[i].trim() && !/^(```|#{1,4}\s|\s*>|\s*([-*]|\d+\.)\s+)/.test(lines[i]) && !(lines[i].includes("|") && isTableSep(lines[i + 1]))) buf.push(lines[i++]);
      const p = el("p", "md-p"); inline(buf.join(" "), ctx, p); root.appendChild(p);
    }
  }
  return root;
}

export { icon };
