// The public demo (`pnpm --dir ui build:demo`): served as plain static files with no backend at all. Every route must
// work from fixtures, with zero requests to /api or any other origin, a visible banner, and axe clean.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import http from "node:http";
import fs from "node:fs";
import path from "node:path";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const AXE = fs.readFileSync(process.env.AXE_JS || require.resolve("axe-core/axe.min.js"), "utf8");
const DIST = path.resolve(import.meta.dirname, "../../ui/dist-demo");
if (!fs.existsSync(path.join(DIST, "index.html"))) throw new Error("ui/dist-demo not built — run `pnpm --dir ui build:demo` first");

const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml" };
let browser, server, base;
const seen = []; // every request the browser made, as "METHOD URL"

before(async () => {
  browser = await chromium.launch();
  server = http.createServer((req, res) => {
    seen.push(`${req.method} ${req.url}`);
    let p = path.join(DIST, decodeURIComponent(new URL(req.url, "http://x").pathname));
    if (!p.startsWith(DIST) || !fs.existsSync(p) || fs.statSync(p).isDirectory()) p = path.join(DIST, "index.html"); // SPA fallback
    res.writeHead(200, { "Content-Type": TYPES[path.extname(p)] || "application/octet-stream" });
    fs.createReadStream(p).pipe(res);
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  base = `http://127.0.0.1:${server.address().port}`;
});
after(async () => { await browser?.close(); server?.close(); });

async function open({ w = 1440, h = 900, scheme = "light", path: p = "/" } = {}) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, reducedMotion: "reduce", colorScheme: scheme });
  const page = await ctx.newPage();
  const offsite = [];
  page.on("request", (r) => { if (!r.url().startsWith(base) && !r.url().startsWith("data:") && !r.url().startsWith("blob:")) offsite.push(r.url()); });
  seen.length = 0;
  await page.goto(base + p, { waitUntil: "networkidle" });
  return { ctx, page, offsite };
}
// A page load of /goals or /automations is a document request the static host answers with index.html; what must never
// happen is any /api or _frag request, or a POST to /goals or /automations.
const apiCalls = () => seen.filter((s) => /^\w+ \/(api|_frag)(\/|\?|$)/.test(s) || /^POST \/(goals|automations)/.test(s));
const axeViolations = async (page) => {
  await page.evaluate(AXE);
  const r = await page.evaluate(() => axe.run(document, { runOnly: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] }));
  return r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(" | ")}`);
};

const ROUTES = ["/", "/overview", "/goals", "/automations", "/skills", "/sandboxes", "/devices", "/runs", "/approvals", "/router", "/audit", "/settings"];

for (const route of ROUTES) {
  test(`@demo ${route}: renders from fixtures, banner visible, zero /api or off-site requests`, async () => {
    const { ctx, page, offsite } = await open({ path: route });
    await page.getByTestId("demo-banner").waitFor();
    await page.locator("main").first().waitFor();
    assert.equal(await page.getByRole("alert").count(), 0, `${route}: no error banner`);
    assert.deepEqual(apiCalls(), [], `${route}: no /api calls`);
    assert.deepEqual(offsite, [], `${route}: no off-site requests`);
    assert.equal(await page.locator("html").getAttribute("data-oc-demo"), "oc-demo-fixtures-v1");
    await ctx.close();
  });
}

test("@demo the server-rendered pages are not offered", async () => {
  const { ctx, page } = await open();
  const hrefs = await page.locator("nav a[href], nav button").evaluateAll((els) => els.map((e) => e.getAttribute("href")).filter(Boolean));
  await page.getByText("Operator").click();
  const all = await page.locator("nav a[href]").evaluateAll((els) => els.map((e) => e.getAttribute("href")));
  for (const h of [...hrefs, ...all]) assert.ok(h !== "/work" && h !== "/workspace", `nav offers ${h}`);
  await ctx.close();
});

test("@demo chat: sending a message is simulated, gets the canned reply, and nothing leaves the page", async () => {
  const { ctx, page, offsite } = await open();
  await page.getByRole("textbox", { name: "Message" }).fill("Plan my week");
  await page.getByRole("button", { name: "Send" }).click();
  await page.getByText("This is the demo, so nothing ran").waitFor({ timeout: 10000 });
  assert.deepEqual(apiCalls(), []);
  assert.deepEqual(offsite, []);
  await page.reload({ waitUntil: "networkidle" });
  assert.equal(await page.getByText("Plan my week").count(), 0, "a reload resets the demo");
  await ctx.close();
});

test("@demo approvals: approve is simulated and the card leaves the queue", async () => {
  const { ctx, page } = await open({ path: "/approvals" });
  const before = await page.locator("[data-approval]").count();
  assert.ok(before >= 2);
  await page.locator("[data-approval] [data-act=approve]").first().click();
  await page.waitForFunction((n) => document.querySelectorAll("[data-approval]").length === n - 1, before, { timeout: 10000 }).catch(async () => {
    // some builds ask for a second press on high-risk items
    await page.locator("[data-approval] [data-act=approve]").first().click();
    await page.waitForFunction((n) => document.querySelectorAll("[data-approval]").length === n - 1, before, { timeout: 10000 });
  });
  assert.deepEqual(apiCalls(), []);
  await ctx.close();
});

test("@demo settings: no sign-out redirect, connecting an app is simulated and stays on the page", async () => {
  const { ctx, page, offsite } = await open({ path: "/settings" });
  assert.equal(await page.getByRole("button", { name: /sign out/i }).count(), 0);
  const connect = page.getByRole("button", { name: /^connect/i }).first();
  if (await connect.count()) {
    await connect.click();
    await page.waitForTimeout(300);
    assert.ok(page.url().startsWith(base), "stayed on the demo host");
  }
  assert.deepEqual(apiCalls(), []);
  assert.deepEqual(offsite, []);
  await ctx.close();
});

test("@demo run page: a finished run renders; download and export links do not leave the page", async () => {
  const { ctx, page } = await open({ path: "/#goal_demo0001" });
  await page.getByText("Read the contracts").first().waitFor({ timeout: 10000 });
  const hrefs = await page.locator("a[href]").evaluateAll((els) => els.map((e) => e.getAttribute("href")));
  for (const h of hrefs) assert.doesNotMatch(h ?? "", /^\/api\//, `link ${h} must not point at /api`);
  assert.deepEqual(apiCalls(), []);
  await ctx.close();
});

for (const scheme of ["light", "dark"]) {
  for (const w of [1440, 390]) {
    test(`@a11y demo axe: 0 violations, ${scheme}, ${w}px, no horizontal scroll`, async () => {
      for (const route of ["/", "/approvals", "/overview", "/audit", "/settings"]) {
        const { ctx, page } = await open({ w, scheme, path: route });
        await page.getByTestId("demo-banner").waitFor();
        assert.deepEqual(await axeViolations(page), [], `${route} ${scheme} ${w}`);
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), `${route} ${w}: no horizontal scroll`);
        await ctx.close();
      }
    });
  }
}
