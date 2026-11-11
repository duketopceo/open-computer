// The Open Computer icon set: original drawings on a 16 grid, drawn with straight segments only, square caps, a 1.5 px
// stroke and `currentColor` (DESIGN.md "Glyphs and icons"). Each icon is a list of segments; `fill` paints a segment solid
// (only the running triangle), `dash` dashes it (queued and skipped). This file has no imports so the sheet script can
// load it directly. `assets/brand/icons.svg` is generated from it (scripts/icon-sheet.mjs) and a test fails on drift.

export interface Seg {
  d: string;
  fill?: boolean;
  dash?: string;
}

const BOX = "M2.5 2.5h11v11h-11z";
const s = (d: string): Seg => ({ d });

const SANDBOX = [s("M2.5 6V2.5h11v11h-11V10"), s("M2.5 8h4")];
const DEVICE = [s("M3 3.5h10v7H3z"), s("M1.5 13h13")];
const PAUSED = [s(BOX), s("M6 5.5v5"), s("M10 5.5v5")];

export const ICONS = {
  // navigation
  chat: [s("M2.5 3.5h11v9h-11z"), s("M5 7h6"), s("M5 9.5h3.5")],
  work: [s("M2.5 5.5h11v8h-11z"), s("M5.5 5.5v-2h5v2")],
  overview: [s("M2.5 2.5h4.5v4.5H2.5z"), s("M9 2.5h4.5v4.5H9z"), s("M2.5 9h4.5v4.5H2.5z"), s("M9 9h4.5v4.5H9z")],
  goals: [s(BOX), s("M6 6h4v4H6z")],
  automations: [s("M3 8V4h8.5"), s("M9.5 2l2.5 2-2.5 2"), s("M13 8v4H4.5"), s("M6.5 10l-2.5 2 2.5 2")],
  skills: [s("M9 2L4 9h4l-1 5 5-7H8z")],
  sandboxes: SANDBOX,
  devices: DEVICE,
  runs: [s("M2 4h6"), s("M2 8h9"), s("M2 12h5"), s("M13 3v10")],
  approvals: PAUSED,
  router: [s("M2 8h5"), s("M7 8l4-4h3"), s("M7 8l4 4h3")],
  audit: [s("M2.5 3.5h11"), s("M2.5 7h6"), s("M2.5 10.5h4"), s("M9 11.5l2 2 3.5-4")],
  workspace: [s(BOX), s("M7 2.5v11")],
  settings: [s("M2 5h12"), s("M2 11h12"), s("M10 3v4"), s("M5 9v4")],
  // actions
  send: [s("M8 13V3"), s("M4 7l4-4 4 4")],
  stop: [s("M4 4h8v8H4z")],
  plus: [s("M8 3v10"), s("M3 8h10")],
  search: [s("M2.5 2.5h8v8h-8z"), s("M10.5 10.5l3 3")],
  retry: [s("M3 8V3.5h8.5"), s("M9.5 1.5l2.5 2-2.5 2"), s("M13 8v4.5H4.5"), s("M6.5 14.5l-2.5-2 2.5-2")],
  archive: [s("M2.5 3.5h11v3h-11z"), s("M3.5 6.5v6h9v-6"), s("M6.5 9h3")],
  delete: [s("M3 4.5h10"), s("M6 4.5v-2h4v2"), s("M4.5 4.5v9h7v-9"), s("M7 7v4"), s("M9 7v4")],
  approve: [s("M3.5 8.5l3 3 6-7")],
  deny: [s("M4 4l8 8"), s("M12 4l-8 8")],
  copy: [s("M5.5 5.5h8v8h-8z"), s("M10.5 5.5v-3h-8v8h3")],
  download: [s("M8 2.5v8"), s("M4.5 7l3.5 3.5L11.5 7"), s("M3 13.5h10")],
  attach: [s("M5 6v6.5h6V4H7.5v6")],
  mic: [s("M6 2.5h4v6.5H6z"), s("M3.5 7.5V10h9V7.5"), s("M8 10v3.5"), s("M5.5 13.5h5")],
  expand: [s("M3.5 6l4.5 4.5L12.5 6")],
  collapse: [s("M3.5 10l4.5-4.5 4.5 4.5")],
  close: [s("M3.5 3.5l9 9"), s("M12.5 3.5l-9 9")],
  menu: [s("M2.5 4h11"), s("M2.5 8h11"), s("M2.5 12h11")],
  external: [s("M9 2.5h4.5V7"), s("M13.5 2.5L7 9"), s("M11 10v3.5H2.5V5H6")],
  // entities
  thread: [s("M2.5 2.5v11"), s("M5.5 4h8"), s("M5.5 8h8"), s("M5.5 12h5")],
  run: [s(BOX), s("M6.5 5.5l4 2.5-4 2.5z")],
  step: [s("M5.5 5.5h5v5h-5z"), s("M8 2v3.5"), s("M8 10.5V14")],
  sandbox: SANDBOX,
  device: DEVICE,
  connector: [s("M5.5 2.5v3.5"), s("M10.5 2.5v3.5"), s("M3.5 6h9v3.5h-9z"), s("M8 9.5v4")],
  artifact: [s("M3.5 2.5h6l3 3v8h-9z"), s("M9.5 2.5v3h3")],
  model: [s("M4.5 4.5h7v7h-7z"), s("M6.5 2v2.5"), s("M9.5 2v2.5"), s("M6.5 11.5V14"), s("M9.5 11.5V14"), s("M2 6.5h2.5"), s("M2 9.5h2.5"), s("M11.5 6.5H14"), s("M11.5 9.5H14")],
  memory: [s("M3 3.5h10v3H3z"), s("M3 7h10v3H3z"), s("M3 10.5h10v3H3z")],
  account: [s("M5.5 2.5h5v5h-5z"), s("M2.5 13.5v-3h11v3")],
  // state: the Flight Strip glyph family (assets/brand/glyphs.svg) plus awaiting, failed, skipped in the same style
  queued: [{ d: BOX, dash: "2.5 2" }],
  leased: [s(BOX), s("M5 8h6")],
  running: [s(BOX), { d: "M6 5l5 3-5 3z", fill: true }],
  blocked: PAUSED,
  awaiting: [s(BOX), s("M5.5 4.5h5l-5 7h5z")],
  denied: [s(BOX), s("M5 5l6 6"), s("M11 5l-6 6")],
  done: [s(BOX), s("M5 8.5l2 2 4-5")],
  failed: [s(BOX), s("M8 4.5v4"), s("M8 10.5v1")],
  skipped: [{ d: BOX, dash: "2.5 2" }, s("M5 11l6-6")],
} satisfies Record<string, Seg[]>;

export type IconName = keyof typeof ICONS;
export const ICON_NAMES = Object.keys(ICONS) as IconName[];
