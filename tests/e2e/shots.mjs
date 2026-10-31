// Screenshots: node tests/e2e/shots.mjs BASE_URL OUT_DIR PREFIX
// Pages x themes x widths (1440, 390). Needs the fixture server (see README).
import { createRequire } from "node:module";
const { chromium } = createRequire(import.meta.url)("playwright"); // honours NODE_PATH
import fs from "node:fs";

const [base, out, prefix = "after"] = process.argv.slice(2);
fs.mkdirSync(out, { recursive: true });
const pages = (process.env.PAGES || "/,/sandboxes,/devices,/runs,/runs/RUNID,/approvals,/router,/audit").split(",");
const browser = await chromium.launch();
for (const theme of ["dark", "light"]) {
  for (const [w, h] of [[1440, 900], [390, 844]]) {
    const ctx = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: theme, reducedMotion: "reduce" });
    await ctx.addCookies([{ name: "oc_sess", value: "test", url: base }]);
    const page = await ctx.newPage();
    await page.addInitScript((t) => { try { localStorage.setItem("oc-theme", t); } catch {} }, theme);
    for (let p of pages) {
      if (p.includes("RUNID")) {
        await page.goto(base + "/runs");
        const href = await page.locator("tbody tr a").first().getAttribute("href").catch(() => null);
        if (!href) continue;
        p = href;
      }
      await page.goto(base + p, { waitUntil: "networkidle" });
      await page.waitForTimeout(150);
      const name = (p === "/" ? "overview" : p.replace(/^\//, "").replace(/\//g, "-").replace(/run_[0-9a-f]+/, "run-log"));
      await page.screenshot({ path: `${out}/${prefix}-${name}-${theme}-${w}.png`, fullPage: false });
    }
    await ctx.close();
  }
}
if (prefix === "after") {
  // open layers, dark and light, desktop
  for (const theme of ["dark", "light"]) {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: theme, reducedMotion: "reduce" });
    await ctx.addCookies([{ name: "oc_sess", value: "test", url: base }]);
    const page = await ctx.newPage();
    await page.goto(base + "/approvals", { waitUntil: "networkidle" });
    await page.keyboard.press("j"); await page.keyboard.press("Enter"); await page.keyboard.press("j"); await page.keyboard.press("r");
    await page.keyboard.type("Redact the SSN before this leaves the box.");
    await page.screenshot({ path: `${out}/after-approvals-note-${theme}-1440.png` });
    await page.keyboard.press("Escape"); await page.keyboard.press("Escape");
    await page.keyboard.press("Control+k"); await page.keyboard.type("run");
    await page.screenshot({ path: `${out}/after-palette-${theme}-1440.png` });
    await page.keyboard.press("Escape"); await page.keyboard.press("?");
    await page.screenshot({ path: `${out}/after-shortcuts-${theme}-1440.png` });
    await page.keyboard.press("Escape");
    await page.goto(base + "/runs/new", { waitUntil: "networkidle" });
    await page.screenshot({ path: `${out}/after-run-confirm-${theme}-1440.png` });
    await ctx.close();
  }
}
await browser.close();
