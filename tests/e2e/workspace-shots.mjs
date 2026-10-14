// Screenshots of the agent workspace: node tests/e2e/workspace-shots.mjs BASE_URL OUT_DIR
// Theme x width (1440, 390) x every sidebar tab, plus an approval docked, an artifact expanded, the cap pause,
// the "N new" pill, the phone bottom sheet and the empty state. Needs the replay fixture (see README).
import { createRequire } from "node:module";
const { chromium } = createRequire(import.meta.url)("playwright");
import fs from "node:fs";

const [base, out] = process.argv.slice(2);
fs.mkdirSync(out, { recursive: true });
const browser = await chromium.launch();

async function open(theme, w, h, goal, q = "speed=0&restart=1") {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: theme, reducedMotion: "reduce" });
  await ctx.addCookies([{ name: "oc_sess", value: "test", url: base }]);
  const page = await ctx.newPage();
  await page.addInitScript((t) => { try { localStorage.setItem("oc-theme", t); } catch {} }, theme);
  await page.goto(`${base}/workspace${goal ? "/" + goal : ""}${q ? "?" + q : ""}`);
  return { ctx, page };
}
const settle = (page) => page.waitForFunction(() => document.getElementById("ws").dataset.stream === "complete" || document.querySelector("#dock .acard"), null, { timeout: 15000 });
const shot = (page, name, theme, w) => page.screenshot({ path: `${out}/ws-${name}-${theme}-${w}.png` });


for (const theme of ["dark", "light"]) {
  for (const [w, h] of [[1440, 900], [390, 844]]) {
    // research: done run with sources, artifacts, cost
    let { ctx, page } = await open(theme, w, h, "g_research01");
    await settle(page);
    await page.waitForTimeout(300);
    await shot(page, "conversation", theme, w);
    if (w < 768) await page.click(`[data-strip="sources"]`);
    for (const t of ["progress", "sources", "artifacts", "box", "cost"]) {
      await page.click(`#tab-${t}`); await page.waitForTimeout(150);
      await shot(page, `tab-${t}`, theme, w);
    }
    // artifact viewer: in the panel then expanded on desktop; full screen with a back chevron on phones
    await page.click(`#tab-artifacts`);
    await page.click(`[data-art="art_report"] .row-btn`);
    if (w >= 768) {
      await page.waitForSelector(".viewer .md h2, .viewer .md h3");
      await shot(page, "artifact-panel", theme, w);
      await page.click('[data-act="art-expand"]');
    }
    await page.waitForSelector("#overlay .viewer .md");
    await page.waitForTimeout(150);
    await shot(page, "artifact-expanded", theme, w);
    await page.keyboard.press("Escape");
    await ctx.close();

    // coding: changes + outbox + the high-risk approval docked
    ({ ctx, page } = await open(theme, w, h, "g_coding01"));
    await page.waitForSelector('#dock [data-approval="ap_clone01"]');
    await page.click('#dock [data-act="goal"]');
    await page.waitForSelector('#dock [data-approval="ap_push01"]');
    await page.waitForTimeout(300);
    await shot(page, "approval-docked", theme, w);
    if (w < 768) await page.click(`[data-strip="outbox"]`);
    for (const t of ["changes", "outbox"]) {
      await page.click(`#tab-${t}`);
      if (t === "changes") { await page.click(".chg-row .row-btn"); }
      await page.waitForTimeout(200);
      await shot(page, `tab-${t}`, theme, w);
    }
    await ctx.close();

    // cap: paused, single action
    ({ ctx, page } = await open(theme, w, h, "g_cap01"));
    await page.waitForSelector("#dock .acard.cap");
    await page.waitForTimeout(300);
    await shot(page, "cap-paused", theme, w);
    await ctx.close();
  }
}

// desktop extras
for (const theme of ["dark", "light"]) {
  let { ctx, page } = await open(theme, 1440, 900, null, "");
  await page.waitForTimeout(600);
  await shot(page, "empty", theme, 1440);
  await ctx.close();
  ({ ctx, page } = await open(theme, 1440, 900, "g_research01"));
  await settle(page);
  await page.keyboard.press("Control+k");
  await page.keyboard.type("source");
  await page.waitForTimeout(200);
  await shot(page, "palette", theme, 1440);
  await page.keyboard.press("Escape");
  await page.keyboard.press("?");
  await page.waitForTimeout(200);
  await shot(page, "shortcuts", theme, 1440);
  await ctx.close();
}
await browser.close();
console.log("shots in", out);
