// Proves what a build contains: `node scripts/check-demo-bundle.mjs <dist> demo|prod`.
//   prod: no demo fixtures anywhere in the emitted JS (the demo must not be switchable on in the real console).
//   demo: the fixtures are present (the build really is the demo).
//   both: index.html has no inline script (the CSP is script-src 'self').
//   demo: every same-origin script, stylesheet or icon index.html references exists in the dist, because the static host has
//   no backend to serve the rest (e.g. /static/theme-init.js, which opsview serves in the real console).
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const MARKER = "oc-demo-fixtures-v1";

/** Pure check. `files` is [{ name, text }] of the emitted JS; `exists(rel)` says whether a dist-relative path exists. */
export function checkBundle({ html, files, exists }, expect) {
  const problems = [];
  if (expect !== "demo" && expect !== "prod") return ["expect must be demo or prod"];
  const has = files.some((f) => f.text.includes(MARKER));
  if (expect === "prod" && has) problems.push(`prod bundle contains the demo marker "${MARKER}" (${files.filter((f) => f.text.includes(MARKER)).map((f) => f.name).join(", ")})`);
  if (expect === "demo" && !has) problems.push(`demo bundle lacks the demo marker "${MARKER}"`);
  if (/<script(?![^>]*\bsrc=)[^>]*>/i.test(html)) problems.push("index.html has an inline <script> (blocked by the CSP)");
  if (expect === "demo") for (const m of html.matchAll(/(?:src|href)="(\/[^"#?]*)"/g)) {
    const rel = m[1].replace(/^\//, "");
    if (rel && !exists(rel)) problems.push(`index.html references /${rel}, which is not in the dist`);
  }
  return problems;
}

export function load(dist) {
  const html = fs.readFileSync(path.join(dist, "index.html"), "utf8");
  const dir = path.join(dist, "assets");
  const files = fs.existsSync(dir)
    ? fs.readdirSync(dir).filter((f) => f.endsWith(".js")).map((f) => ({ name: f, text: fs.readFileSync(path.join(dir, f), "utf8") }))
    : [];
  return { html, files, exists: (rel) => fs.existsSync(path.join(dist, rel)) };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const [dist, expect] = process.argv.slice(2);
  const problems = checkBundle(load(path.resolve(dist ?? "dist")), expect);
  if (problems.length) {
    for (const p of problems) console.error("FAIL " + p);
    process.exit(1);
  }
  console.log(`bundle check (${expect}): ok`);
}
