// Artifact viewers: one per type, all built with DOM nodes (no innerHTML) from same-origin bytes.
// HTML artifacts run in a sandboxed iframe with no network; see routes.py ARTIFACT_CSP and the frame-src rule.
import { $, el, icon, num, bytes } from "./util.js";
import { render as renderMarkdown } from "./markdown.js";
import { render as renderMermaid } from "./mermaid.js";

export const KIND_LABEL = { markdown: "Document", html: "App", image: "Image", svg: "Image", pdf: "PDF", csv: "Table", code: "Code", text: "Text", diff: "Diff", mermaid: "Diagram", archive: "Archive", office: "Office", audio: "Audio", video: "Video" };
export const KIND_ORDER = ["markdown", "pdf", "html", "csv", "image", "svg", "mermaid", "code", "diff", "text", "archive", "office", "audio", "video"];

const fetchText = (url) => fetch(url, { credentials: "same-origin" }).then((r) => { if (!r.ok) throw new Error("HTTP " + r.status); return r.text(); });
const fetchJson = (url) => fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } }).then((r) => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); });
const assetUrl = (a, extra) => a.url + "?v=" + a.version + (extra || "");

function failed(host, msg) {
  host.textContent = "";
  const p = el("p", "viewer-error", msg || "This artifact couldn't be loaded. Reload to try again.", { role: "alert" });
  host.appendChild(p);
}

// ---- CSV ---------------------------------------------------------------------------------------------
export function parseCsv(text, limit) {
  const rows = []; let row = [], cur = "", q = false, i = 0, total = 0;
  const lines = text.split(/\r?\n/).filter((l) => l && !l.startsWith("#"));
  // Fast path for big files: only parse `limit`+1 lines, count the rest.
  total = lines.length - 1;
  for (const line of lines.slice(0, limit + 1)) {
    row = []; cur = ""; q = false;
    for (i = 0; i < line.length; i++) {
      const c = line[i];
      if (q) { if (c === '"' && line[i + 1] === '"') { cur += '"'; i++; } else if (c === '"') q = false; else cur += c; }
      else if (c === '"') q = true;
      else if (c === ",") { row.push(cur); cur = ""; }
      else cur += c;
    }
    row.push(cur); rows.push(row);
  }
  return { head: rows[0] || [], rows: rows.slice(1), total };
}

function csvView(host, a) {
  return fetchText(assetUrl(a)).then((t) => {
    const { head, rows, total } = parseCsv(t, 100);
    const shown = rows.length, all = a.rows || total;
    host.appendChild(el("p", "viewer-note", `Showing ${num(shown)} of ${num(all)} rows`, { "data-csv-count": "" }));
    const wrap = el("div", "table-scroll", null, { tabindex: "0", role: "region", "aria-label": "Rows of " + a.name });
    const table = el("table", "dense data");
    table.appendChild(el("caption", "sr", `First ${shown} rows of ${a.name}`));
    const tr = el("tr"); head.forEach((h) => tr.appendChild(el("th", "", h, { scope: "col" })));
    table.appendChild(el("thead")).appendChild(tr);
    const tb = el("tbody");
    rows.forEach((r) => { const x = el("tr"); r.forEach((c) => x.appendChild(el("td", "mono", c))); tb.appendChild(x); });
    table.appendChild(tb); wrap.appendChild(table); host.appendChild(wrap);
  });
}

// ---- code / text with Preview and Source ---------------------------------------------------------------
const KW = /\b(def|class|return|import|from|if|elif|else|for|while|in|not|and|or|is|None|True|False|with|as|try|except|raise|lambda|yield|const|let|var|function|export|async|await|new|this|null|true|false)\b/g;
function tint(line, parent) {
  const m = /^(\s*)(#.*|\/\/.*)$/.exec(line);
  if (m) { parent.appendChild(document.createTextNode(m[1])); parent.appendChild(el("span", "tk-c", m[2])); return; }
  const re = /("[^"\n]*"|'[^'\n]*')|(\b\d+(?:\.\d+)?\b)|(\b(?:def|class|return|import|from|if|elif|else|for|while|in|not|and|or|is|None|True|False|with|as|try|except|raise|lambda|yield|const|let|var|function|export|async|await|new|this|null|true|false)\b)/g;
  let i = 0, x;
  while ((x = re.exec(line))) {
    if (x.index > i) parent.appendChild(document.createTextNode(line.slice(i, x.index)));
    parent.appendChild(el("span", x[1] ? "tk-s" : x[2] ? "tk-n" : "tk-k", x[0]));
    i = x.index + x[0].length;
  }
  if (i < line.length) parent.appendChild(document.createTextNode(line.slice(i)));
}
void KW;

function codeView(host, a, toggle) {
  return fetchText(assetUrl(a)).then((t) => {
    const preview = el("div", "code-preview", null, { tabindex: "0", role: "region", "aria-label": "Preview of " + a.name });
    const lines = t.replace(/\n$/, "").split("\n");
    lines.forEach((ln, i) => {
      const row = el("div", "code-line"); row.dataset.line = String(i + 1);
      row.appendChild(el("span", "ln-no", String(i + 1), { "aria-hidden": "true" }));
      const tx = el("span", "ln-tx mono"); if (a.kind === "code") tint(ln, tx); else tx.textContent = ln; row.appendChild(tx);
      preview.appendChild(row);
    });
    const src = el("pre", "code-source mono", t, { tabindex: "0", "aria-label": "Source of " + a.name });
    src.hidden = true;
    host.appendChild(preview); host.appendChild(src);
    toggle(["Preview", "Source"], (i) => { preview.hidden = i !== 0; src.hidden = i !== 1; });
    host.__goto = (n) => { const r = preview.querySelector('[data-line="' + n + '"]'); if (r) { r.scrollIntoView({ block: "center" }); r.classList.add("hit"); } };
  });
}

// ---- diff ------------------------------------------------------------------------------------------------
export function parseDiff(text) {
  const files = []; let f = null, h = null, oldN = 0, newN = 0;
  for (const raw of String(text).split("\n")) {
    let m;
    if (raw.startsWith("--- ")) { f = { old: raw.slice(4).replace(/^a\//, ""), path: "", hunks: [] }; files.push(f); h = null; continue; }
    if (raw.startsWith("+++ ") && f) { f.path = raw.slice(4).replace(/^b\//, ""); continue; }
    if ((m = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$/.exec(raw)) && f) { h = { header: raw, lines: [] }; oldN = +m[1]; newN = +m[2]; f.hunks.push(h); continue; }
    if (!h) continue;
    if (raw.startsWith("+")) h.lines.push({ t: "add", o: "", n: newN++, x: raw.slice(1) });
    else if (raw.startsWith("-")) h.lines.push({ t: "del", o: oldN++, n: "", x: raw.slice(1) });
    else if (raw.startsWith(" ")) h.lines.push({ t: "ctx", o: oldN++, n: newN++, x: raw.slice(1) });
  }
  return files;
}

export function diffView(text, opts) {
  opts = opts || {};
  const root = el("div", "diff");
  parseDiff(text).forEach((f, fi) => {
    const file = el("section", "diff-file", null, { "aria-label": "Diff of " + f.path });
    if (!opts.noPath) file.appendChild(el("h3", "diff-path mono", f.path));
    f.hunks.forEach((h, hi) => {
      const hid = (opts.idPrefix || "") + fi + "." + hi;
      const box = el("div", "hunk"); box.dataset.hunk = hid;
      const hd = el("div", "hunk-head");
      hd.appendChild(el("span", "mono", h.header));
      if (opts.review) {
        const lab = el("label", "hunk-review");
        const cb = el("input", "", null, { type: "checkbox", "data-fid": "hunk-" + hid });
        cb.checked = opts.review.has(hid);
        cb.addEventListener("change", () => opts.onReview && opts.onReview(hid, cb.checked));
        lab.appendChild(cb); lab.appendChild(el("span", "", "Reviewed")); hd.appendChild(lab);
      }
      box.appendChild(hd);
      const tbl = el("div", "hunk-body mono", null, { role: "group", "aria-label": "Hunk " + h.header, tabindex: "0" });
      h.lines.forEach((l) => {
        const row = el("div", "dl dl-" + l.t); if (l.n !== "") row.dataset.new = String(l.n);
        row.appendChild(el("span", "dl-o", String(l.o), { "aria-hidden": "true" }));
        row.appendChild(el("span", "dl-n", String(l.n), { "aria-hidden": "true" }));
        const sign = el("span", "dl-s");
        sign.appendChild(el("span", "", l.t === "add" ? "+" : l.t === "del" ? "\u2212" : " ", { "aria-hidden": "true" }));
        if (l.t !== "ctx") sign.appendChild(el("span", "sr", l.t === "add" ? "added " : "removed "));
        row.appendChild(sign);
        row.appendChild(el("span", "dl-x", l.x));
        tbl.appendChild(row);
      });
      box.appendChild(tbl); file.appendChild(box);
    });
    root.appendChild(file);
  });
  if (!root.children.length) root.appendChild(el("p", "viewer-note", "No changes in this diff."));
  return root;
}

// ---- viewer shell --------------------------------------------------------------------------------------------
export function openViewer(a, ctx) {
  const root = el("div", "viewer"); root.dataset.viewer = a.kind; root.dataset.artifact = a.id;
  const bar = el("div", "viewer-bar");
  const body = el("div", "viewer-body");
  const meta = el("p", "viewer-meta caption muted");
  const stepTitle = a.step && ctx.stepTitle ? ctx.stepTitle(a.step) : "";
  meta.textContent = `${KIND_LABEL[a.kind] || a.kind} · version ${a.version}${a.size != null ? " · " + bytes(a.size) : ""}${stepTitle ? " · from “" + stepTitle + "”" : ""}`;
  const tools = el("div", "viewer-tools");
  bar.appendChild(meta); bar.appendChild(tools);
  root.appendChild(bar); root.appendChild(body);

  const toggle = (labels, onPick) => {
    const g = el("div", "seg", null, { role: "group", "aria-label": "View" });
    labels.forEach((l, i) => {
      const b = el("button", "seg-btn", l, { type: "button", "aria-pressed": i === 0 ? "true" : "false" });
      b.addEventListener("click", () => { Array.from(g.children).forEach((c, j) => c.setAttribute("aria-pressed", j === i ? "true" : "false")); onPick(i); });
      g.appendChild(b);
    });
    tools.prepend(g);
  };

  const needsApproval = a.export && a.export.requires_approval;
  const ap = ctx.exportApproval ? ctx.exportApproval(a) : null;
  const exportBtn = (label) => {
    if (needsApproval && !(ap && ap.status === "approved")) {
      const pending = ap && ap.status === "pending";
      const b = el("button", "btn", pending ? "Export waiting for approval" : "Approve to export", { type: "button", "data-act": "export-request", "data-artifact": a.id });
      if (pending) b.setAttribute("aria-disabled", "true");
      b.prepend(icon("external"));
      tools.appendChild(b);
    } else {
      const b = el("a", "btn", label || "Export", { href: assetUrl(a, "&download=1"), download: a.name });
      b.prepend(icon("external")); tools.appendChild(b);
    }
  };

  const k = a.kind;
  let p = Promise.resolve();
  if (k === "markdown") { p = fetchText(assetUrl(a)).then((t) => { const d = el("div", "md"); d.appendChild(renderMarkdown(t, ctx.mdCtx)); body.appendChild(d); }); exportBtn("Export"); }
  else if (k === "html") {
    const f = el("iframe", "frame", null, { sandbox: "allow-scripts", src: assetUrl(a), title: "Preview of " + a.name, referrerpolicy: "no-referrer", loading: "lazy" });
    body.appendChild(f);
    const open = el("a", "btn", "Open in new tab", { href: assetUrl(a), target: "_blank", rel: "noopener noreferrer", title: "Opens the sandboxed page on its own, still with no network" });
    open.prepend(icon("external")); tools.appendChild(open);
    body.appendChild(el("p", "viewer-note", "Runs sandboxed with no network access. Nothing in it can reach the internet or this console."));
  } else if (k === "image" || k === "svg") {
    const img = el("img", "img", null, { src: assetUrl(a), alt: a.summary || a.name });
    body.appendChild(el("div", "img-wrap")).appendChild(img); exportBtn("Export");
  } else if (k === "pdf") {
    body.appendChild(el("iframe", "frame pdf", null, { src: assetUrl(a), title: "Document view of " + a.name }));
    exportBtn("Export");
  } else if (k === "csv") { p = csvView(body, a); exportBtn("Export"); }
  else if (k === "code" || k === "text") { p = codeView(body, a, toggle); exportBtn("Export"); }
  else if (k === "diff") { p = fetchText(assetUrl(a)).then((t) => body.appendChild(diffView(t))); exportBtn("Export patch"); }
  else if (k === "mermaid") {
    p = fetchText(assetUrl(a)).then((t) => {
      const svg = renderMermaid(t);
      const prev = el("div", "mmd-wrap", null, { tabindex: "0", role: "region", "aria-label": "Diagram " + a.name });
      const src = el("pre", "code-source mono", t, { tabindex: "0", "aria-label": "Diagram source" }); src.hidden = !!svg;
      if (svg) prev.appendChild(svg); else prev.hidden = true;
      if (!svg) body.appendChild(el("p", "viewer-note", "This diagram uses syntax the built-in renderer doesn't support, so the source is shown."));
      body.appendChild(prev); body.appendChild(src);
      if (svg) toggle(["Preview", "Source"], (i) => { prev.hidden = i !== 0; src.hidden = i !== 1; });
    });
    exportBtn("Export");
  } else if (k === "archive") {
    p = fetchJson(a.url + "/listing").then((j) => {
      body.appendChild(el("p", "viewer-note", `${num(j.total)} entries · ${bytes(j.bytes)} compressed`));
      const wrap = el("div", "table-scroll", null, { tabindex: "0", role: "region", "aria-label": "Files in " + a.name });
      const t = el("table", "dense data"); t.appendChild(el("caption", "sr", "Files in " + a.name));
      const tr = el("tr"); ["Path", "Size"].forEach((h, i) => tr.appendChild(el("th", i ? "num" : "", h, { scope: "col" })));
      t.appendChild(el("thead")).appendChild(tr);
      const tb = el("tbody");
      j.entries.forEach((e) => { const r = el("tr"); r.appendChild(el("td", "mono", e.path)); r.appendChild(el("td", "num", e.dir ? "—" : bytes(e.size))); tb.appendChild(r); });
      t.appendChild(tb); wrap.appendChild(t); body.appendChild(wrap);
    });
    exportBtn("Download archive");
  } else if (k === "audio" || k === "video") {
    body.appendChild(el(k, "media", null, { controls: "", src: assetUrl(a), "aria-label": a.name })); exportBtn("Export");
  } else {
    body.appendChild(el("p", "viewer-note", "A read-only preview of this file type isn't available offline. Download it to open it elsewhere.")); exportBtn("Download");
  }
  p.catch(() => failed(body));
  root.__ready = p;
  return root;
}

export { $ };
