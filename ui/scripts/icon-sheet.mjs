// Renders the icon set as a reviewable sheet: `node scripts/icon-sheet.mjs` writes assets/brand/icons.svg and
// icons-dark.svg from src/components/icons/paths.ts (Node strips the types). A test fails if the committed files drift.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { ICONS, ICON_NAMES } from "../src/components/icons/paths.ts";

const here = path.dirname(fileURLToPath(import.meta.url));
export const SHEET_DIR = path.resolve(here, "../../assets/brand");
const COLS = 8;
const CELL_W = 80;
const CELL_H = 64;

export function renderSheet(dark) {
  const bg = dark ? "#101316" : "#EDEFF0";
  const ink = dark ? "#E9ECEE" : "#14181B";
  const muted = dark ? "#9AA5AD" : "#4A545B";
  const rows = Math.ceil(ICON_NAMES.length / COLS);
  const w = COLS * CELL_W;
  const h = rows * CELL_H;
  const cells = ICON_NAMES.map((name, i) => {
    const x = (i % COLS) * CELL_W;
    const y = Math.floor(i / COLS) * CELL_H;
    const segs = ICONS[name]
      .map((g) => `<path d="${g.d}"${g.fill ? ' fill="currentColor"' : ""}${g.dash ? ` stroke-dasharray="${g.dash}"` : ""}/>`)
      .join("");
    return `<g transform="translate(${x} ${y})"><g transform="translate(${CELL_W / 2 - 16} 8) scale(2)" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="square" stroke-linejoin="miter" style="color:${ink}">${segs}</g><text x="${CELL_W / 2}" y="54" text-anchor="middle" font-family="monospace" font-size="9" fill="${muted}">${name}</text></g>`;
  }).join("");
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" role="img" aria-label="Open Computer icon set"><title>Open Computer icon set</title><rect width="${w}" height="${h}" fill="${bg}"/>${cells}</svg>\n`;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  fs.writeFileSync(path.join(SHEET_DIR, "icons.svg"), renderSheet(false));
  fs.writeFileSync(path.join(SHEET_DIR, "icons-dark.svg"), renderSheet(true));
  console.log(`wrote ${ICON_NAMES.length} icons to ${SHEET_DIR}`);
}
