#!/usr/bin/env node
// Design rule: no purple, violet or indigo anywhere in the product's tokens, CSS, SVG, TypeScript or Rust UI colours.
// Fails on (1) colour words and (2) any hex, rgb() or hsl() colour whose hue is in the purple band (225 to 325 degrees)
// with visible saturation. Greys, black and white never match. Test files that assert the rule are skipped by name.
import { readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const ROOTS = ["ui/src", "ui/index.html", "desktop/src", "desktop/crates", "core/packages", "services/opsview/static", "assets", "edge"];
const EXT = new Set([".css", ".svg", ".ts", ".tsx", ".rs", ".json", ".html", ".toml"]);
const SKIP_DIR = new Set(["node_modules", "target", "dist", "dist-demo", "out", ".git"]);
const SKIP_FILE = /(\.test\.|\.spec\.|check-no-purple|palette\.test|pnpm-lock|package-lock|Cargo\.lock)/;
const WORDS = /\b(purple|violet|indigo|lavender|amethyst|grape|plum|orchid)\b/i;

/** Hue in degrees and saturation 0..1 from 0..255 channels. */
export function hueSat(r, g, b) {
  const [R, G, B] = [r / 255, g / 255, b / 255];
  const max = Math.max(R, G, B);
  const min = Math.min(R, G, B);
  const d = max - min;
  if (d === 0) return { h: 0, s: 0 };
  const l = (max + min) / 2;
  const s = d / (1 - Math.abs(2 * l - 1));
  let h;
  if (max === R) h = ((G - B) / d) % 6;
  else if (max === G) h = (B - R) / d + 2;
  else h = (R - G) / d + 4;
  h = (h * 60 + 360) % 360;
  return { h, s };
}

export const isPurple = ({ h, s }) => s > 0.15 && h >= 225 && h <= 325;

export function findViolations(text) {
  const out = [];
  const lines = text.split("\n");
  lines.forEach((line, i) => {
    const w = WORDS.exec(line);
    if (w) out.push({ line: i + 1, why: `colour word "${w[0]}"` });
    for (const m of line.matchAll(/#([0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b/g)) {
      let hex = m[1];
      if (hex.length === 3) hex = hex.split("").map((c) => c + c).join("");
      const v = hueSat(parseInt(hex.slice(0, 2), 16), parseInt(hex.slice(2, 4), 16), parseInt(hex.slice(4, 6), 16));
      if (isPurple(v)) out.push({ line: i + 1, why: `hex ${m[0]} is purple (hue ${Math.round(v.h)})` });
    }
    for (const m of line.matchAll(/rgba?\(\s*(\d{1,3})[\s,]+(\d{1,3})[\s,]+(\d{1,3})/g)) {
      const v = hueSat(+m[1], +m[2], +m[3]);
      if (isPurple(v)) out.push({ line: i + 1, why: `${m[0]}) is purple (hue ${Math.round(v.h)})` });
    }
    for (const m of line.matchAll(/hsla?\(\s*(\d{1,3}(?:\.\d+)?)(?:deg)?[\s,]+(\d{1,3}(?:\.\d+)?)%/g)) {
      const h = +m[1];
      const s = +m[2] / 100;
      if (isPurple({ h, s })) out.push({ line: i + 1, why: `${m[0]} is purple` });
    }
    for (const m of line.matchAll(/Color32::from_rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/g)) {
      const v = hueSat(+m[1], +m[2], +m[3]);
      if (isPurple(v)) out.push({ line: i + 1, why: `${m[0]}) is purple` });
    }
  });
  return out;
}

function* walk(p) {
  const st = statSync(p, { throwIfNoEntry: false });
  if (!st) return;
  if (st.isFile()) {
    yield p;
    return;
  }
  for (const name of readdirSync(p)) {
    if (SKIP_DIR.has(name)) continue;
    yield* walk(path.join(p, name));
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  let bad = 0;
  for (const r of ROOTS) {
    for (const f of walk(path.join(root, r))) {
      if (!EXT.has(path.extname(f)) || SKIP_FILE.test(path.basename(f))) continue;
      for (const v of findViolations(readFileSync(f, "utf8"))) {
        bad += 1;
        console.error(`${path.relative(root, f)}:${v.line}: ${v.why}`);
      }
    }
  }
  if (bad > 0) {
    console.error(`\n${bad} purple colour(s) found. The palette is black and white (see DESIGN.md).`);
    process.exit(1);
  }
  console.log("no purple, violet or indigo found");
}
