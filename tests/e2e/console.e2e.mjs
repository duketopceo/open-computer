// Browser tests for the console: axe in both themes at 1440 and 390, keyboard-only
// journeys, palette coverage, log windowing, layout shift. Runs against fixture data
// (tests/opsview/serve_fixture.py): no microVMs, no models, no network beyond 127.0.0.1.
//
//   cd tests/e2e && npm i && npx playwright install chromium && npm test
// Without npm: NODE_PATH=<dir containing playwright and axe-core> node --test console.e2e.mjs
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const AXE = fs.readFileSync(process.env.AXE_JS || require.resolve("axe-core/axe.min.js"), "utf8");
const REPO = path.resolve(import.meta.dirname, "../..");
const PY = (process.env.OC_PYTHON || "uv run --with fido2 python").split(" ");

const servers = [];
async function serve(port, ...extra) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "oc-e2e-"));
  const p = spawn(PY[0], [...PY.slice(1), "-I", "tests/opsview/serve_fixture.py", String(port), dir, ...extra], { cwd: REPO });
  servers.push(p);
  p.stderr.on("data", (d) => process.stderr.write(d)); // surface fixture crashes in CI logs
  await new Promise((res, rej) => {
    p.stdout.on("data", (d) => String(d).includes("ready") && res());
    p.on("exit", (c) => rej(new Error("fixture exited " + c)));
    setTimeout(() => rej(new Error("fixture timeout")), 30000);
  });
  return `http://127.0.0.1:${port}`;
}

let browser, RO, RW;
before(async () => {
  browser = await chromium.launch();
  [RO, RW] = await Promise.all([serve(8861), serve(8862)]);
});
after(async () => { await browser?.close(); servers.forEach((s) => s.kill()); });

async function open(base, p, { theme = "dark", w = 1440, h = 900 } = {}) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: theme, reducedMotion: "reduce" });
  await ctx.addCookies([{ name: "oc_sess", value: "test", url: base }]);
  const page = await ctx.newPage();
  await page.addInitScript((t) => { try { if (!localStorage.getItem("oc-theme")) localStorage.setItem("oc-theme", t); } catch {} }, theme);
  await page.goto(base + p, { waitUntil: "networkidle" });
  return { ctx, page };
}

const ROUTES = ["/", "/sandboxes", "/runs", "/runs/new", "/approvals", "/router", "/audit"];

for (const theme of ["dark", "light"]) {
  for (const w of [1440, 390]) {
    test(`@a11y axe: 0 violations, ${theme}, ${w}px, every route`, async () => {
      for (const route of [...ROUTES, "RUN"]) {
        let p = route;
        const { ctx, page } = await open(RO, "/runs", { theme, w });
        if (route === "RUN") p = await page.locator("tbody tr a").first().getAttribute("href");
        await page.goto(RO + p, { waitUntil: "networkidle" });
        await page.evaluate(AXE);
        const r = await page.evaluate(() => axe.run(document, { runOnly: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"] }));
        assert.deepEqual(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(" | ")}`), [], `${route} ${theme} ${w}`);
        await ctx.close();
      }
    });
  }
}

test("@a11y axe on open layers (palette, shortcuts, note editor)", async () => {
  for (const theme of ["dark", "light"]) {
    const { ctx, page } = await open(RO, "/approvals", { theme });
    for (const open of [() => page.keyboard.press("Control+k"), async () => { await page.keyboard.press("Escape"); await page.keyboard.press("?"); }, async () => { await page.keyboard.press("Escape"); await page.keyboard.press("j"); await page.keyboard.press("r"); }]) {
      await open();
      await page.evaluate(AXE);
      const r = await page.evaluate(() => axe.run(document, { runOnly: ["wcag2a", "wcag2aa", "wcag22aa"] }));
      assert.deepEqual(r.violations.map((v) => v.id), [], theme);
    }
    await ctx.close();
  }
});

test("@palette lists 100% of the actions available on each screen", async () => {
  for (const route of ROUTES) {
    const { ctx, page } = await open(RO, route);
    const expected = (await page.evaluate(() => JSON.parse(document.getElementById("oc-actions").textContent))).filter((a) => a.id !== "palette").map((a) => a.label);
    await page.keyboard.press("Control+k");
    const got = await page.locator("[role=option] .lbl").allTextContents();
    assert.deepEqual(got.sort(), expected.sort(), route);
    await ctx.close();
  }
});

test("@keyboard shortcuts: ? overlay, g-nav, palette, Esc returns focus", async () => {
  const { ctx, page } = await open(RO, "/");
  await page.keyboard.press("?");
  const dlg = page.getByRole("dialog", { name: "Keyboard shortcuts" });
  await dlg.waitFor();
  assert.ok((await dlg.locator("kbd").count()) >= 12);
  await dlg.getByRole("textbox").fill("move");
  assert.equal(await dlg.locator("li").count(), 2);
  await page.keyboard.press("Escape");
  assert.equal(await dlg.count(), 0);
  for (const [seq, url] of [["g r", "/approvals"], ["g n", "/runs"], ["g x", "/sandboxes"], ["g u", "/router"], ["g e", "/audit"]]) {
    await Promise.all([page.waitForURL(RO + url), page.keyboard.press(seq.split(" ")[0]).then(() => page.keyboard.press(seq.split(" ")[1]))]);
  }
  await page.keyboard.press("Control+k");
  await page.getByRole("combobox", { name: "Command palette" }).fill("blocked");
  await Promise.all([page.waitForURL(/f=blocked/), page.keyboard.press("Enter")]);
  // Esc from the palette hands focus back to the element that opened it
  await page.locator("#main").focus();
  await page.keyboard.press("Control+k");
  await page.keyboard.press("Escape");
  assert.equal(await page.evaluate(() => document.activeElement.id), "main");
  await page.keyboard.press("g"); await Promise.all([page.waitForURL(RO + "/"), page.keyboard.press("o")]);
  await ctx.close();
});

test("@keyboard approvals: oldest first, a approves, r needs a note, keyboard only", async () => {
  const { ctx, page } = await open(RW, "/approvals");
  const whos = await page.locator("tbody.item .who span").allTextContents();
  assert.deepEqual(whos, ["docs-agent", "deploy-agent", "research-agent"]);
  await page.keyboard.press("j");
  assert.equal(await page.locator("[data-cur]").count(), 1);
  await page.keyboard.press("Enter");                       // opens payload
  assert.ok(await page.locator("tbody.item").first().locator(".item-detail").isVisible());
  await page.keyboard.press("j");                           // move on: deploy-agent (high risk)
  await page.keyboard.press("r");                           // send back opens the note
  const ta = page.locator("tbody.item").nth(1).locator("textarea");
  assert.ok(await ta.evaluate((e) => e === document.activeElement));
  await page.keyboard.press("Control+Enter");               // empty note is refused
  assert.match(await page.locator("tbody.item").nth(1).locator(".err").textContent(), /Add a note/);
  assert.equal(await page.locator("tbody.item").count(), 3);
  await page.keyboard.type("Redact the SSN before this leaves.");
  await page.keyboard.press("Control+Enter");
  await page.waitForFunction(() => document.querySelectorAll("tbody.item").length === 2);
  await page.keyboard.press("k"); await page.keyboard.press("k");
  await page.keyboard.press("a");                           // approve whichever row the cursor is on
  await page.waitForFunction(() => document.querySelectorAll("tbody.item").length === 1);
  assert.match(await page.locator("#toasts").textContent(), /Approved .*'s request/);
  await page.keyboard.press("Escape");
  const audit = await (await ctx.request.get(RW + "/audit?f=decisions")).text();
  assert.match(audit, /approved/); assert.match(audit, /sent back/);
  await ctx.close();
});

test("@keyboard no pointer needed to start a run: c, confirm page, Tab to Run", async () => {
  const { ctx, page } = await open(RW, "/runs");
  await Promise.all([page.waitForURL(/runs\/new/), page.keyboard.press("c")]);
  assert.match(await page.locator("main").textContent(), /Boots real microVMs/);
  await page.locator("#go").focus();
  await Promise.all([page.waitForURL(/\/runs\/run_/), page.keyboard.press("Enter")]);
  await ctx.close();
});

test("@log virtualized viewer: line numbers, 5,000-line DOM cap, reaches the last line", async () => {
  const { ctx, page } = await open(RO, "/runs");
  const hrefs = await page.locator("tbody tr a").evaluateAll((as) => as.map((a) => a.getAttribute("href")));
  let big;
  for (const h of hrefs) { await page.goto(RO + h); if ((await page.locator("#log").getAttribute("data-lines")) === "12001") { big = h; break; } }
  assert.ok(big, "fixture run with 12,001 lines");
  const nodes = () => page.locator("#log .ln").count();
  assert.ok((await nodes()) > 0 && (await nodes()) <= 5000);
  assert.ok((await nodes()) < 400, "windowed, not 12,001 rows");
  const font = await page.locator("#log .tx").first().evaluate((e) => { const s = getComputedStyle(e); return [s.fontSize, s.lineHeight]; });
  assert.deepEqual(font, ["12px", "18px"]);
  assert.equal((await page.locator("#log .no").first().textContent()), "1");
  await page.locator("#log").focus();
  await page.keyboard.press("End");
  await page.waitForFunction(() => document.querySelector("#log .ln:last-child .no")?.textContent === "12001");
  assert.ok((await nodes()) <= 5000);
  await ctx.close();
});

test("@perf no layout shift when the sandbox skeleton resolves", async () => {
  const { ctx, page } = await open(RO, "/", {});
  await page.addInitScript(() => { window.__cls = 0; new PerformanceObserver((l) => { for (const e of l.getEntries()) if (!e.hadRecentInput) window.__cls += e.value; }).observe({ type: "layout-shift", buffered: true }); });
  await page.goto(RO + "/sandboxes", { waitUntil: "networkidle" });
  await page.waitForSelector("table.dense");
  assert.ok((await page.evaluate(() => window.__cls)) <= 0.02);
  assert.equal(await page.locator("table.dense tbody tr").first().evaluate((e) => e.getBoundingClientRect().height), 32);
  await ctx.close();
});

test("no horizontal page scroll at 390px and theme toggle persists", async () => {
  for (const route of ROUTES) {
    const { ctx, page } = await open(RO, route, { w: 390, h: 844 });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), route);
    await ctx.close();
  }
  const { ctx, page } = await open(RO, "/", {});
  await page.keyboard.press("Control+k");
  await page.keyboard.type("theme");
  await page.keyboard.press("Enter");
  await page.reload();
  assert.equal(await page.evaluate(() => document.documentElement.getAttribute("data-theme")), "light");
  await ctx.close();
});
