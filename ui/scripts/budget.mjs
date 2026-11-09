// Byte budget for the built SPA: `node scripts/budget.mjs [distDir]` after `vite build`.
// The entry chunk (the script index.html loads) must stay under `main`; every other JS chunk needs its own
// entry in budget.json, keyed by the chunk's name before the content hash, so a new lazy chunk cannot slip in
// without a budget. Sizes are gzip level 9, which is close to what a static host serves.
import fs from "node:fs";
import path from "node:path";
import { gzipSync } from "node:zlib";
import { fileURLToPath } from "node:url";

/** Chunk name without Vite's `-<hash>.js` suffix. */
export const chunkName = (file) => path.basename(file).replace(/-[A-Za-z0-9_-]{8}\.js$/, "").replace(/\.js$/, "");

/** Pure check: `chunks` is [{ file, gzip, entry }]. Returns a list of plain-language problems (empty = pass). */
export function checkBudget(chunks, budget) {
  const problems = [];
  for (const c of chunks) {
    const name = chunkName(c.file);
    const limit = c.entry ? budget.main : budget.chunks?.[name];
    if (limit == null) problems.push(`${c.file}: no budget for chunk "${name}" in budget.json`);
    else if (c.gzip > limit) problems.push(`${c.file}: ${c.gzip} B gzip is over its ${limit} B budget`);
  }
  if (!chunks.some((c) => c.entry)) problems.push("no entry chunk found in index.html");
  return problems;
}

export function measure(dist) {
  const html = fs.readFileSync(path.join(dist, "index.html"), "utf8");
  const entries = new Set([...html.matchAll(/<script[^>]+src="\/?([^"]+\.js)"/g)].map((m) => m[1]));
  const dir = path.join(dist, "assets");
  return fs
    .readdirSync(dir)
    .filter((f) => f.endsWith(".js"))
    .map((f) => ({
      file: f,
      gzip: gzipSync(fs.readFileSync(path.join(dir, f)), { level: 9 }).length,
      entry: entries.has(`assets/${f}`),
    }));
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  const dist = path.resolve(process.argv[2] || path.join(root, "dist"));
  const budget = JSON.parse(fs.readFileSync(path.join(root, "budget.json"), "utf8"));
  const chunks = measure(dist);
  for (const c of chunks) console.log(`${c.entry ? "entry " : "chunk "} ${c.file}  ${(c.gzip / 1024).toFixed(1)} kB gzip`);
  const problems = checkBudget(chunks, budget);
  if (problems.length) {
    for (const p of problems) console.error(`budget: ${p}`);
    process.exit(1);
  }
  console.log("budget: ok");
}
