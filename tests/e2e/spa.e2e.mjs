// Browser tests for the React front door at `/` (ui/dist must be built:
// `pnpm --dir ui build`). Fixture server only; every goal/transcribe call is
// intercepted with page.route, so nothing spends money or leaves 127.0.0.1.
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

if (!fs.existsSync(path.join(REPO, "ui/dist/index.html"))) {
  throw new Error("ui/dist not built — run `pnpm --dir ui build` first");
}

let browser, base, server;
before(async () => {
  browser = await chromium.launch();
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "oc-spa-"));
  server = spawn(PY[0], [...PY.slice(1), "-I", "tests/opsview/serve_fixture.py", "8891", dir], { cwd: REPO });
  server.stderr.on("data", (d) => process.stderr.write(d));
  await new Promise((res, rej) => {
    server.stdout.on("data", (d) => String(d).includes("ready") && res());
    server.on("exit", (c) => rej(new Error("fixture exited " + c)));
    setTimeout(() => rej(new Error("fixture timeout")), 30000);
  });
  base = "http://127.0.0.1:8891";
});
after(async () => { await browser?.close(); server?.kill(); });

const json = (status, body) => ({ status, contentType: "application/json", body: JSON.stringify(body) });

async function open({ w = 1440, h = 900, routes = {}, scheme = "light", setup } = {}) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, reducedMotion: "reduce", colorScheme: scheme });
  await ctx.addCookies([{ name: "oc_sess", value: "test", url: base }]);
  const page = await ctx.newPage();
  // Empty runs list by default so the composer (not a run) is the landing view.
  await page.route("**/api/runs", (r) => r.fulfill(json(200, { runs: [] })));
  for (const [glob, handler] of Object.entries(routes)) await page.route(glob, handler);
  if (setup) await setup(page);
  await page.goto(base + "/", { waitUntil: "networkidle" });
  return { ctx, page };
}

const RUN = {
  run: "g_e2e01", goal: "say hi", origin: "console", device_id: null, declared: 1, worker_failures: [],
  subtasks: [{ subtask: "s1", title: "say hi", state: "DONE", route: "cheap", engine: "jev", placement: "cloud-a",
    reason: "", gate_required: false, cost: 0.000026, pii: false, result: "hi", ts: 1, finished_ts: 2 }],
};

for (const w of [1440, 390]) {
  test(`@a11y spa axe: 0 violations at ${w}px`, async () => {
    const { ctx, page } = await open({ w });
    await page.getByRole("textbox", { name: "Describe a goal" }).waitFor();
    await page.evaluate(AXE);
    const r = await page.evaluate(() => axe.run(document, { runOnly: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] }));
    assert.deepEqual(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(" | ")}`), [], `${w}`);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    await ctx.close();
  });
}

test("@flow double Run sends one goal; failed confirm keeps the card and retries", async () => {
  let posts = 0;
  let failConfirm = true;
  const { ctx, page } = await open({
    routes: {
      "**/goals": async (r) => {
        posts++;
        const body = JSON.parse(r.request().postData() || "{}");
        if (body.confirm !== "1") {
          await new Promise((res) => setTimeout(res, 300)); // window for a double click
          return r.fulfill(json(200, { needs_confirm: true, cap: 5 }));
        }
        if (failConfirm) return r.fulfill(json(500, { error: "orchestrator down" }));
        return r.fulfill(json(200, { run: "g_e2e01" }));
      },
      "**/api/runs/g_e2e01": (r) => r.fulfill(json(200, RUN)),
    },
  });
  await page.getByRole("textbox", { name: "Describe a goal" }).fill("say hi");
  const run = page.getByRole("button", { name: "Run", exact: true });
  await run.click();
  await run.click({ force: true, timeout: 1000 }).catch(() => {}); // second click while pending
  await page.getByRole("heading", { name: "Run this?" }).waitFor();
  assert.equal(posts, 1, "pending submit must block a second request");

  await page.getByRole("button", { name: "Run it" }).click();
  await page.getByRole("alert").filter({ hasText: "orchestrator down" }).waitFor();
  assert.ok(await page.getByRole("heading", { name: "Run this?" }).isVisible(), "card kept after failure");

  failConfirm = false;
  await page.getByRole("button", { name: "Try again" }).click();
  await page.getByText("<$0.0001").first().waitFor(); // micro-cost renders nonzero
  assert.equal(await page.getByRole("heading", { name: "Run this?" }).count(), 0);
  await ctx.close();
});

test("@errors unreachable API shows the connection banner", async () => {
  const { ctx, page } = await open({ routes: { "**/api/runs": (r) => r.abort("connectionrefused") } });
  await page.getByRole("status").filter({ hasText: "Connection lost" }).waitFor();
  await ctx.close();
});

test("@errors expired session shows sign-in and disables the composer", async () => {
  const { ctx, page } = await open({ routes: { "**/api/session": (r) => r.fulfill(json(401, { error: "Sign in again." })) } });
  await page.getByRole("status").filter({ hasText: "Your session expired" }).waitFor();
  await page.getByRole("textbox", { name: "Describe a goal" }).fill("anything");
  assert.ok(await page.getByRole("button", { name: "Run", exact: true }).isDisabled());
  await ctx.close();
});

test("@errors a missing run offers retry instead of spinning forever", async () => {
  const { ctx, page } = await open({
    routes: {
      "**/api/runs": (r) => r.fulfill(json(200, { runs: [{ run: "g_gone", goal: "x", origin: "console", device_id: null, subtasks: 1, states: {}, ts: 1 }] })),
      "**/api/runs/g_gone": (r) => r.fulfill(json(404, { error: "no such run" })),
    },
  });
  await page.getByRole("alert").filter({ hasText: "no longer exists" }).waitFor();
  await page.getByRole("button", { name: "New goal" }).click();
  await page.getByRole("textbox", { name: "Describe a goal" }).waitFor();
  await ctx.close();
});

// ---------------------------------------------------------------------------
// Ported console pages: Devices, Sandboxes (+ detail), Router, Audit.
// Real fixture data unless a test intercepts an /api call; the router tester POST
// is always intercepted, so nothing routes or spends.
// ---------------------------------------------------------------------------
async function openAt(path, { w = 1440, h = 900, routes = {} } = {}) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, reducedMotion: "reduce" });
  await ctx.addCookies([{ name: "oc_sess", value: "test", url: base }]);
  const page = await ctx.newPage();
  for (const [glob, handler] of Object.entries(routes)) await page.route(glob, handler);
  await page.goto(base + path, { waitUntil: "networkidle" });
  return { ctx, page };
}

async function axeViolations(page) {
  await page.evaluate(AXE);
  const r = await page.evaluate(() => axe.run(document, { runOnly: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"] }));
  return r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(" | ")}`);
}

const noHScroll = (page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);

const DEVICES = {
  total: 3, online: 1, eligible: 1, parked_depth: 2,
  devices: [
    { device_id: "omarchy-max", os: "omarchy-aarch64", capabilities: ["browser", "fs"], roots: ["/home/luke/src"], online: true, awake: true, eligible: true, state: "online", last_heartbeat: Date.now() / 1000 - 30, active_grants: 1, inbox_depth: 0 },
    { device_id: "<img src=x onerror=window.__xss=1>", os: "<b>bold</b>", capabilities: [], roots: [], online: true, awake: false, eligible: false, state: "asleep", last_heartbeat: Date.now() / 1000 - 600, active_grants: 0, inbox_depth: 3 },
    { device_id: "stale-box", os: "linux", capabilities: ["fs"], roots: [], online: false, awake: false, eligible: false, state: "offline", last_heartbeat: 0, active_grants: 0, inbox_depth: 0 },
  ],
};
const PAID = { paid: true, dest_host: "openrouter.ai", per_route_cost: 0.000004, spend: 0.01, cap: 1, ledger: [] };

const PAGES = [
  ["/devices", "Devices"],
  ["/sandboxes", "Sandboxes"],
  ["/sandboxes/sbx-7f3a91c2", "Sandbox"],
  ["/router", "Router"],
  ["/audit", "Audit"],
  ["/audit?f=blocked&q=gpt", "Audit"],
  // Slices 1-4 of the React move: these used to be server-rendered and axe-checked in console.e2e.mjs.
  ["/overview", "Overview"],
  ["/runs", "Runs"],
  ["/approvals", "Approvals"],
  ["/skills", "Skills"],
  ["/automations", "Automations"],
  ["/no-such-page", "Not found"],
];

for (const w of [1440, 390]) {
  test(`@a11y spa ported pages: axe 0 violations and no horizontal scroll at ${w}px`, async () => {
    for (const [path, title] of PAGES) {
      // /no-such-page is a server 404; render the in-app not-found page by client navigation instead
      const target = path === "/no-such-page" ? "/devices" : path;
      const { ctx, page } = await openAt(target, { w });
      if (path === "/no-such-page") {
        await page.evaluate(() => { history.pushState(null, "", "/no-such-page"); dispatchEvent(new PopStateEvent("popstate")); });
      }
      await page.getByRole("heading", { level: 1, name: title }).waitFor();
      await page.getByRole("status").waitFor({ state: "detached" }).catch(() => {});
      assert.deepEqual(await axeViolations(page), [], `${path} @${w}`);
      assert.ok(await noHScroll(page), `${path} @${w} scrolls horizontally`);
      await ctx.close();
    }
  });
}

test("@a11y spa ported pages: error, empty, confirm and open-menu states have 0 violations", async () => {
  // error state with Retry
  let { ctx, page } = await openAt("/audit", { routes: { "**/api/audit*": (r) => r.fulfill(json(500, { error: "audit unreadable" })) } });
  await page.getByRole("alert").filter({ hasText: "audit unreadable" }).waitFor();
  assert.deepEqual(await axeViolations(page), [], "error state");
  await ctx.close();
  // empty states
  ({ ctx, page } = await openAt("/devices"));
  await page.getByText("No devices paired").waitFor();
  assert.deepEqual(await axeViolations(page), [], "devices empty");
  await ctx.close();
  // paid confirm panel
  ({ ctx, page } = await openAt("/router", {
    routes: {
      "**/api/router": (r) => r.fulfill(json(200, PAID)),
      "**/api/router/test": (r) => r.fulfill(json(200, { needs_confirm: true, dest_host: "openrouter.ai", cost: 0.000004 })),
    },
  }));
  await page.getByRole("textbox", { name: "Utterance to route" }).fill("secret plan");
  await page.getByRole("button", { name: /^Route/ }).click();
  await page.getByRole("heading", { name: "Send this to the decider?" }).waitFor();
  assert.deepEqual(await axeViolations(page), [], "confirm panel");
  await ctx.close();
  // mobile menu open
  ({ ctx, page } = await openAt("/devices", { w: 390 }));
  await page.getByRole("button", { name: "Menu" }).click();
  await page.getByRole("link", { name: "Audit" }).waitFor();
  assert.deepEqual(await axeViolations(page), [], "mobile menu open");
  await ctx.close();
});

test("@nav every SPA nav entry deep-links with one current item; unported entries are plain links", async () => {
  const { ctx, page } = await openAt("/devices");
  const nav = page.getByRole("navigation", { name: "Console" });
  const entries = await nav.getByRole("link").evaluateAll((as) => as.map((a) => [a.textContent, a.getAttribute("href")]));
  assert.deepEqual(entries.map(([t]) => t), ["Computer", "Work", "Overview", "Goals", "Automations", "Skills", "Sandboxes", "Devices", "Runs", "Approvals", "Router", "Audit", "Workspace"]);
  for (const href of ["/", "/devices", "/sandboxes", "/router", "/audit"]) {
    const { ctx: c2, page: p2 } = await openAt(href);
    if (href !== "/") {
      assert.equal(await p2.getByRole("navigation", { name: "Console" }).locator('[aria-current="page"]').count(), 1, href);
      assert.equal(await p2.locator("h1").count(), 1, href);
    }
    await c2.close();
  }
  await ctx.close();
});

test("@nav client-side navigation keeps the document, retitles, moves focus, and back/forward work", async () => {
  const { ctx, page } = await openAt("/sandboxes");
  await page.evaluate(() => { window.__same = true; });
  const nav = page.getByRole("navigation", { name: "Console" });
  await nav.getByRole("link", { name: "Devices" }).click();
  await page.waitForURL(base + "/devices");
  await page.getByRole("heading", { level: 1, name: "Devices" }).waitFor();
  assert.equal(await page.title(), "Devices · open-computer");
  assert.equal(await page.evaluate(() => document.activeElement?.id), "main");
  await nav.getByRole("link", { name: "Audit" }).click();
  await page.waitForURL(base + "/audit");
  await page.goBack();
  await page.waitForURL(base + "/devices");
  await page.getByRole("heading", { level: 1, name: "Devices" }).waitFor();
  await page.goForward();
  await page.getByRole("heading", { level: 1, name: "Audit" }).waitFor();
  assert.equal(await page.evaluate(() => window.__same), true, "no document reload happened");
  // a ctrl-click is left to the browser (new tab), the SPA does not intercept it
  const before = page.url();
  await nav.getByRole("link", { name: "Router" }).click({ modifiers: ["Control"] });
  assert.equal(page.url(), before);
  // an unported page is a full navigation to the server-rendered console
  await Promise.all([page.waitForURL(base + "/runs"), nav.getByRole("link", { name: "Runs" }).click()]);
  assert.equal(await page.evaluate(() => window.__same), undefined, "Runs is a real page load");
  await ctx.close();
});

test("@nav in-app not-found offers a way home", async () => {
  const { ctx, page } = await openAt("/devices");
  await page.evaluate(() => { history.pushState(null, "", "/nope"); dispatchEvent(new PopStateEvent("popstate")); });
  await page.getByRole("heading", { level: 1, name: "Not found" }).waitFor();
  await page.getByRole("link", { name: "Back to the computer" }).click();
  await page.getByRole("textbox", { name: "Describe a goal" }).waitFor();
  await ctx.close();
});

test("devices: rows, states as words, empty fixture, and hostile text stays text", async () => {
  let { ctx, page } = await openAt("/devices", { routes: { "**/api/devices": (r) => r.fulfill(json(200, DEVICES)) } });
  await page.getByRole("table", { name: "Devices, state and queue depth" }).waitFor();
  await page.getByText("omarchy-aarch64").waitFor();
  assert.ok(await page.getByText("<img src=x onerror=window.__xss=1>").isVisible(), "device id rendered as literal text");
  assert.equal(await page.evaluate(() => window.__xss), undefined);
  assert.equal(await page.locator("table img, table b").count(), 0);
  const table = await page.getByRole("table", { name: "Devices, state and queue depth" }).innerText();
  for (const word of ["online", "asleep", "offline"]) assert.match(table, new RegExp(`●?◐?○?\\s*${word}`));
  await page.getByText("never").waitFor();
  assert.match(await page.locator("main").innerText(), /1 of 3 eligible now/);
  await ctx.close();
  ({ ctx, page } = await openAt("/devices"));
  await page.getByText("No devices paired").waitFor();
  await ctx.close();
});

test("sandboxes: list, client-side detail, missing id, and honest control-plane states", async () => {
  let { ctx, page } = await openAt("/sandboxes");
  await page.getByRole("table", { name: "Sandboxes" }).waitFor();
  assert.ok(await page.getByRole("link", { name: "sbx-7f3a91c2" }).isVisible());
  assert.ok((await page.getByText("code-interpreter").count()) >= 1);
  await page.evaluate(() => { window.__same = true; });
  await page.getByRole("link", { name: "sbx-7f3a91c2" }).click();
  await page.waitForURL(base + "/sandboxes/sbx-7f3a91c2");
  await page.getByRole("table", { name: "Sandbox sbx-7f3a91c2" }).waitFor();
  assert.ok(await page.getByText("templateID").isVisible());
  assert.equal(await page.evaluate(() => window.__same), true);
  assert.equal(await page.getByRole("navigation", { name: "Console" }).locator('[aria-current="page"]').textContent(), "Sandboxes");
  await ctx.close();

  ({ ctx, page } = await openAt("/sandboxes/does-not-exist"));
  await page.getByText("Sandbox not found").waitFor();
  await page.getByRole("link", { name: "Back to sandboxes" }).click();
  await page.getByRole("table", { name: "Sandboxes" }).waitFor();
  await ctx.close();

  ({ ctx, page } = await openAt("/sandboxes", { routes: { "**/api/sandboxes": (r) => r.fulfill(json(200, { configured: true, up: false, api_host: "cube.test", sandboxes: [] })) } }));
  await page.getByText("unknown, not empty").waitFor();
  await ctx.close();
  ({ ctx, page } = await openAt("/sandboxes", { routes: { "**/api/sandboxes": (r) => r.fulfill(json(200, { configured: false, up: false, api_host: "", sandboxes: [] })) } }));
  await page.getByText("CUBE_API_URL").waitFor();
  await ctx.close();
  ({ ctx, page } = await openAt("/sandboxes", { routes: { "**/api/sandboxes": (r) => r.fulfill(json(200, { configured: true, up: true, api_host: "cube.test", sandboxes: [] })) } }));
  await page.getByText("No sandboxes running").waitFor();
  await ctx.close();
});

test("sandboxes: ids with markup or slashes are encoded in links and rendered as text", async () => {
  const evil = "a/b<i>x</i>";
  const payload = { configured: true, up: true, api_host: "cube.test", sandboxes: [{ sandboxID: evil, templateID: "t", status: "running", note: { n: "<script>window.__xss=1</script>" } }] };
  const { ctx, page } = await openAt("/sandboxes", { routes: { "**/api/sandboxes": (r) => r.fulfill(json(200, payload)) } });
  const link = page.getByRole("link", { name: evil });
  const href = await link.getAttribute("href");
  assert.equal(href, "/sandboxes/" + encodeURIComponent(evil));
  await link.click();
  await page.getByRole("table", { name: "Sandbox " + evil }).waitFor();
  // loading the encoded detail URL directly (a hard load, not a client navigation) works too
  await page.goto(new URL(href, page.url()).href, { waitUntil: "networkidle" });
  await page.getByRole("table", { name: "Sandbox " + evil }).waitFor();
  assert.equal(await page.evaluate(() => window.__xss), undefined);
  assert.equal(await page.locator("main i, main script").count(), 0);
  await ctx.close();
});

test("@perf sandboxes: no layout shift when the inventory replaces the loading state", async () => {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });
  await ctx.addCookies([{ name: "oc_sess", value: "test", url: base }]);
  const page = await ctx.newPage();
  await page.addInitScript(() => { window.__cls = 0; new PerformanceObserver((l) => { for (const e of l.getEntries()) if (!e.hadRecentInput) window.__cls += e.value; }).observe({ type: "layout-shift", buffered: true }); });
  await page.goto(base + "/sandboxes", { waitUntil: "networkidle" });
  await page.getByRole("table", { name: "Sandboxes" }).waitFor();
  assert.ok((await page.evaluate(() => window.__cls)) <= 0.02);
  await ctx.close();
});

test("router: free engine routes immediately and refetches the ledger (POST intercepted)", async () => {
  let posts = [];
  let routerGets = 0;
  const { ctx, page } = await openAt("/router", {
    routes: {
      "**/api/router": (r) => {
        routerGets++;
        return r.fulfill(json(200, { paid: false, dest_host: "", per_route_cost: 0, spend: 0, cap: 1,
          ledger: routerGets === 1 ? [] : [{ ts: Date.now() / 1000, event: "decision", route: "just-routed", engine: "table", cost: 0 }] }));
      },
      "**/api/router/test": async (r) => {
        posts.push(JSON.parse(r.request().postData() || "{}"));
        await new Promise((res) => setTimeout(res, 200)); // window for a double click
        return r.fulfill(json(200, { result: { route: "local", tier: "cheap", engine: "table", confidence: 0.9, cost: 0, ms: 1 } }));
      },
    },
  });
  await page.getByText("No decisions yet").waitFor();
  await page.getByRole("textbox", { name: "Utterance to route" }).fill("summarise this");
  const go = page.getByRole("button", { name: /^Route/ });
  await go.click();
  await go.click({ force: true, timeout: 500 }).catch(() => {});
  await page.getByRole("table", { name: "Route result" }).waitFor();
  assert.equal(posts.length, 1, "double submit sends one request");
  assert.equal(posts[0].confirm, undefined);
  await page.getByRole("cell", { name: "just-routed" }).waitFor(); // the ledger was refetched
  assert.equal(routerGets, 2);
  assert.ok(await page.getByText("nothing leaves this box").isVisible());
  await ctx.close();
});

test("router: a paid router asks first, sends nothing until confirmed, Cancel sends nothing (POST intercepted)", async () => {
  const posts = [];
  const { ctx, page } = await openAt("/router", {
    routes: {
      "**/api/router": (r) => r.fulfill(json(200, PAID)),
      "**/api/router/test": (r) => {
        const body = JSON.parse(r.request().postData() || "{}");
        posts.push(body);
        if (body.confirm !== "1") return r.fulfill(json(200, { needs_confirm: true, dest_host: "openrouter.ai", cost: 0.000004 }));
        return r.fulfill(json(200, { result: { route: "frontier", tier: "big", engine: "decider", confidence: 0.8, cost: 0.000004, ms: 120 } }));
      },
    },
  });
  await page.getByText("sends the utterance to openrouter.ai").waitFor();
  await page.getByRole("textbox", { name: "Utterance to route" }).fill("secret plan");
  await page.getByRole("button", { name: /^Route/ }).click();
  await page.getByRole("heading", { name: "Send this to the decider?" }).waitFor();
  assert.ok(await page.getByText("openrouter.ai", { exact: true }).first().isVisible());
  assert.deepEqual(posts.map((p) => p.confirm), [undefined]); // asked, not sent
  await page.getByRole("button", { name: "Cancel" }).click();
  await page.getByRole("textbox", { name: "Utterance to route" }).waitFor();
  assert.equal(posts.length, 1, "cancel sends nothing");
  await page.getByRole("button", { name: /^Route/ }).click();
  await page.getByRole("button", { name: /^Route it/ }).click();
  await page.getByRole("table", { name: "Route result" }).waitFor();
  assert.deepEqual(posts.map((p) => p.confirm), [undefined, undefined, "1"]);
  await ctx.close();
});

test("router: a failed route says nothing was sent and keeps the utterance", async () => {
  const { ctx, page } = await openAt("/router", {
    routes: { "**/api/router/test": (r) => r.fulfill(json(502, { error: "Routing failed: budget halted. Nothing was sent." })) },
  });
  const box = page.getByRole("textbox", { name: "Utterance to route" });
  await box.fill("hello there");
  await page.getByRole("button", { name: /^Route/ }).click();
  await page.getByRole("alert").filter({ hasText: "Nothing was sent." }).waitFor();
  assert.equal(await box.inputValue(), "hello there");
  await ctx.close();
});

test("router: the real tester API refuses a POST without the CSRF header", async () => {
  const { ctx, page } = await openAt("/router");
  const status = await page.evaluate(async () => (await fetch("/api/router/test", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ q: "hi" }) })).status);
  assert.equal(status, 403);
  await ctx.close();
});

test("audit: deep-link filters, URL-driven filtering without reload, back restores, hostile text stays text", async () => {
  let { ctx, page } = await openAt("/audit?f=blocked&q=gpt");
  await page.getByRole("table", { name: "Audit events, newest first" }).waitFor();
  assert.equal(await page.getByRole("link", { name: "Blocked" }).getAttribute("aria-current"), "true");
  assert.equal(await page.getByRole("searchbox", { name: "Filter audit rows" }).inputValue(), "gpt");
  assert.ok((await page.locator("tbody tr").count()) >= 1);
  assert.equal(await page.locator("tbody tr", { hasNotText: "blocked" }).count(), 0);
  await page.evaluate(() => { window.__same = true; });
  await page.getByRole("link", { name: "Allowed" }).click();
  await page.waitForURL(base + "/audit?f=allowed&q=gpt");
  await page.getByRole("link", { name: "Allowed" }).and(page.locator('[aria-current="true"]')).waitFor();
  await page.goBack();
  await page.waitForURL(/f=blocked/);
  await page.getByRole("link", { name: "Blocked" }).and(page.locator('[aria-current="true"]')).waitFor();
  assert.equal(await page.evaluate(() => window.__same), true);
  await page.getByRole("searchbox", { name: "Filter audit rows" }).fill("zzz-no-such-text");
  await page.getByRole("button", { name: "Filter" }).click();
  await page.getByText("No matching events").waitFor();
  await ctx.close();

  const rows = { f: "all", q: "", total: 1, shown: 1, rows: [{ ts: Date.now() / 1000, event: "note", result: "recorded", detail: "what=<script>window.__xss=1</script><b>x</b>" }] };
  ({ ctx, page } = await openAt("/audit", { routes: { "**/api/audit*": (r) => r.fulfill(json(200, rows)) } }));
  await page.getByText("what=<script>window.__xss=1</script><b>x</b>").waitFor();
  assert.equal(await page.evaluate(() => window.__xss), undefined);
  assert.equal(await page.locator("td b").count(), 0);
  await page.getByText("Showing the latest 1 of 1 matching events.").waitFor();
  await ctx.close();
});

test("audit: a failed fetch offers Retry, and only the last filter's rows render", async () => {
  let fail = true;
  const { ctx, page } = await openAt("/audit", {
    routes: {
      "**/api/audit*": async (r) => {
        const u = new URL(r.request().url());
        if (fail) return r.fulfill(json(500, { error: "audit unreadable" }));
        if (u.searchParams.get("f") === "blocked") await new Promise((res) => setTimeout(res, 400)); // slow, stale response
        return r.fulfill(json(200, { f: u.searchParams.get("f") ?? "all", q: "", total: 1, shown: 1, rows: [{ ts: 1, event: "e-" + (u.searchParams.get("f") ?? "all"), result: "recorded", detail: "" }] }));
      },
    },
  });
  await page.getByRole("alert").filter({ hasText: "audit unreadable" }).waitFor();
  fail = false;
  await page.getByRole("button", { name: "Retry" }).click();
  await page.getByText("e-all", { exact: true }).waitFor();
  await page.getByRole("link", { name: "Blocked" }).click();
  await page.getByRole("link", { name: "Allowed" }).click();
  await page.getByText("e-allowed", { exact: true }).waitFor();
  await page.waitForTimeout(600);
  assert.equal(await page.getByText("e-blocked", { exact: true }).count(), 0, "stale response dropped");
  await ctx.close();
});

test("an expired session on a ported page shows the session-expired state", async () => {
  const { ctx, page } = await openAt("/devices", { routes: { "**/api/devices": (r) => r.fulfill(json(401, { error: "Sign in again." })) } });
  await page.getByRole("alert").filter({ hasText: "session expired" }).waitFor();
  await ctx.close();
});

test("nav: Sandboxes is current only for declared routes, and a same-URL click adds no history entry", async () => {
  let { ctx, page } = await openAt("/sandboxes/sbx-7f3a91c2");
  assert.equal(await page.getByRole("navigation", { name: "Console" }).locator('[aria-current="page"]').textContent(), "Sandboxes");
  await page.evaluate(() => { history.pushState(null, "", "/sandboxes/a/b"); dispatchEvent(new PopStateEvent("popstate")); });
  await page.getByRole("heading", { level: 1, name: "Not found" }).waitFor();
  assert.equal(await page.getByRole("navigation", { name: "Console" }).locator('[aria-current="page"]').count(), 0);
  await ctx.close();
  ({ ctx, page } = await openAt("/devices"));
  const before = await page.evaluate(() => history.length);
  await page.getByRole("navigation", { name: "Console" }).getByRole("link", { name: "Devices" }).click();
  assert.equal(await page.evaluate(() => history.length), before, "clicking the current page's link pushes nothing");
  await ctx.close();
});

test("focus: the first client-side navigation from / lands on main; an audit filter submit keeps focus in the input", async () => {
  let { ctx, page } = await open();
  await page.getByRole("navigation", { name: "Operator" }).getByRole("link", { name: "Devices" }).click();
  await page.getByRole("heading", { level: 1, name: "Devices" }).waitFor();
  assert.equal(await page.evaluate(() => document.activeElement?.id), "main");
  await ctx.close();
  ({ ctx, page } = await openAt("/audit"));
  const box = page.getByRole("searchbox", { name: "Filter audit rows" });
  await box.fill("gpt");
  await box.press("Enter");
  await page.waitForURL(/q=gpt/);
  await page.getByRole("table", { name: "Audit events, newest first" }).waitFor();
  assert.equal(await page.evaluate(() => document.activeElement?.id), "audit-q", "focus stays in the filter input");
  await ctx.close();
});

test("audit: rows from the previous filter are not shown while the next one loads; a bad timestamp does not crash", async () => {
  const row = (event, ts) => ({ ts, event, result: "recorded", detail: "" });
  let release;
  const gate = new Promise((res) => { release = res; });
  const { ctx, page } = await openAt("/audit", {
    routes: {
      "**/api/audit*": async (r) => {
        const f = new URL(r.request().url()).searchParams.get("f") ?? "all";
        if (f === "blocked") await gate;
        return r.fulfill(json(200, { f, q: "", total: 2, shown: 2, rows: [row("e-" + f, 1), row("bad-ts", 1e16)] }));
      },
    },
  });
  await page.getByText("e-all", { exact: true }).waitFor();
  assert.ok(await page.getByText("invalid time").isVisible(), "an out-of-range timestamp renders a fallback instead of throwing");
  await page.getByRole("link", { name: "Blocked" }).click();
  await page.getByRole("status").filter({ hasText: "Loading" }).waitFor();
  assert.equal(await page.getByText("e-all", { exact: true }).count(), 0, "previous filter's rows are hidden while loading");
  release();
  await page.getByText("e-blocked", { exact: true }).waitFor();
  await ctx.close();
});

test("router: a failed second route clears the first route's result", async () => {
  let n = 0;
  const { ctx, page } = await openAt("/router", {
    routes: {
      "**/api/router/test": (r) => (++n === 1
        ? r.fulfill(json(200, { result: { route: "local", tier: "cheap", engine: "table", confidence: 0.9, cost: 0, ms: 1 } }))
        : r.fulfill(json(502, { error: "Routing failed: budget halted. Nothing was sent." }))),
    },
  });
  const box = page.getByRole("textbox", { name: "Utterance to route" });
  await box.fill("first");
  await page.getByRole("button", { name: /^Route/ }).click();
  await page.getByRole("table", { name: "Route result" }).waitFor();
  await box.fill("second");
  await page.getByRole("button", { name: /^Route/ }).click();
  await page.getByRole("alert").filter({ hasText: "Nothing was sent." }).waitFor();
  assert.equal(await page.getByRole("table", { name: "Route result" }).count(), 0);
  await ctx.close();
});
// ---- U5: agent jobs view (every route mocked; nothing reaches the orchestrator) ----

const J1 = "aaaaaaaaaa", J2 = "bbbbbbbbbb", J3 = "cccccccccc";
const future = () => Date.now() / 1000 + 3600;
const JOBS = () => [
  { job: J1, goal: "migrate the billing tables", started: Date.now() / 1000 - 600, status: "running", budget: 25, spent: 1.2, awaiting_approval: true, pending: 2 },
  { job: J2, goal: "queued research job", started: Date.now() / 1000 - 60, status: "queued", budget: 25, spent: 0, awaiting_approval: false, pending: 0 },
  { job: J3, goal: "write release notes", started: Date.now() / 1000 - 9000, status: "finished", budget: 10, spent: 3.5, awaiting_approval: false, pending: 0 },
];
const APPROVALS = () => [
  { id: "ap_plan", kind: "plan", detail: { steps: ["inspect schema", "write migration"] }, hash: "h".repeat(64), ts: Date.now() / 1000 - 30, expires_at: future() },
  { id: "ap_egr", kind: "egress", detail: { host: "api.example.com" }, hash: "e".repeat(64), ts: Date.now() / 1000 - 20, expires_at: future() },
];
const DETAIL = (approvals) => ({
  job: J1, goal: "migrate the billing tables", status: "running", budget: 25, spent: 1.2,
  events: [{ job: J1, seq: 1, ts: Date.now() / 1000 - 500, event: "job_start", goal: "migrate the billing tables" },
    { job: J1, seq: 2, ts: Date.now() / 1000 - 400, event: "decision", round: 1, action: "plan" }],
  approvals,
});

// One handler for list, detail and the three POST routes; `o` supplies overrides
// and `o.posts` records what the SPA sent.
function agentApi(o = {}) {
  o.posts ??= [];
  const handler = async (r) => {
    const req = r.request();
    const p = new URL(req.url()).pathname;
    const m = req.method();
    if (m === "GET" && p === "/api/agent-jobs") return r.fulfill(await (o.list ? o.list() : json(200, { jobs: JOBS() })));
    const d = p.match(/^\/api\/agent-jobs\/(\w+)$/);
    if (m === "GET" && d) return r.fulfill(await (o.detail ? o.detail(d[1]) : json(200, d[1] === J1 ? DETAIL(APPROVALS()) : { ...DETAIL([]), job: d[1], events: [] })));
    if (m === "POST") {
      const rec = { path: p, headers: req.headers(), body: JSON.parse(req.postData() || "{}") };
      o.posts.push(rec);
      if (p === "/api/agent-jobs") return r.fulfill(await (o.submit ? o.submit(rec.body) : json(200, { job: J2 })));
      return r.fulfill(await (o.decide ? o.decide(rec) : json(200, { ok: true })));
    }
    return r.continue();
  };
  o.setup = (page) => page.route(/\/(api\/)?agent-jobs/, handler);
  return o;
}

const goAgents = async (page) => page.getByRole("button", { name: /^Agent jobs/ }).click();
const card = (page, kind) => page.getByRole("listitem", { name: new RegExp(`Approval needed: ${kind}`, "i") });

test("@agents list shows statuses, queued as not running, pending badges and the payload card", async () => {
  const api = agentApi();
  const { ctx, page } = await open({ setup: api.setup });
  await page.getByRole("button", { name: /^Agent jobs\s*2/ }).waitFor(); // sidebar pending count
  await goAgents(page);
  await page.getByText("queued · not running").waitFor();
  await page.getByText("finished", { exact: true }).waitFor();
  await page.getByText("awaiting approval", { exact: true }).waitFor(); // row badge
  assert.ok(await page.getByText("$1.20 of $25").isVisible());
  await page.getByRole("button", { name: /migrate the billing tables/ }).click();
  const egress = card(page, "egress");
  await egress.waitFor();
  assert.ok((await egress.textContent()).includes("api.example.com"), "exact detail shown");
  assert.ok((await card(page, "plan").textContent()).includes("write migration"));
  assert.equal(await page.getByText("job_start").count() > 0, true, "trail timeline renders");
  await ctx.close();
});

test("@agents empty list, empty trail, and failed list shows inline retry", async () => {
  let fail = true;
  const api = agentApi({
    list: () => (fail ? json(500, { error: "orchestrator store unreadable" }) : json(200, { jobs: [{ ...JOBS()[2] }] })),
  });
  const { ctx, page } = await open({ setup: api.setup });
  await goAgents(page);
  await page.getByRole("alert").filter({ hasText: "orchestrator store unreadable" }).waitFor();
  fail = false;
  await page.getByRole("button", { name: "Retry" }).click();
  await page.getByRole("button", { name: /write release notes/ }).click();
  await page.getByText("No events yet").waitFor();
  await ctx.close();

  const empty = agentApi({ list: () => json(200, { jobs: [] }) });
  const b = await open({ setup: empty.setup });
  await goAgents(b.page);
  await b.page.getByText("No agent jobs yet").waitFor();
  await b.ctx.close();
});

test("@agents Approve sends the displayed hash and CSRF once; 409 keeps the card and asks for reload", async () => {
  let stale = true;
  let approved = false;
  const api = agentApi({
    detail: () => json(200, DETAIL(approved ? APPROVALS().slice(1) : APPROVALS())),
    decide: async () => {
      await new Promise((res) => setTimeout(res, 300)); // window for a double click
      if (stale) return json(409, { error: "approval is stale" });
      approved = true;
      return json(200, { ok: true });
    },
  });
  const { ctx, page } = await open({ setup: api.setup });
  await goAgents(page);
  await page.getByRole("button", { name: /migrate the billing tables/ }).click();
  const plan = card(page, "plan");
  await plan.waitFor();
  const csrf = await page.evaluate(() => fetch("/api/session").then((r) => r.json()).then((j) => j.csrf));
  const approve = plan.getByRole("button", { name: "Approve", exact: true });
  await approve.click();
  await approve.click({ force: true, timeout: 500 }).catch(() => {}); // second press while in flight
  await plan.getByRole("alert").filter({ hasText: "Reload to review" }).waitFor();
  assert.equal(api.posts.length, 1, "double click sends one request");
  const post = api.posts[0];
  assert.equal(post.path, `/api/agent-jobs/${J1}/approvals/ap_plan/approve`);
  assert.equal(post.body.hash, "h".repeat(64));
  assert.ok(csrf && post.headers["x-csrf-token"] === csrf, "CSRF header sent");
  assert.ok(await plan.isVisible(), "card kept after 409");
  assert.ok(await approve.isDisabled(), "stale card not actionable until reloaded");

  stale = false;
  await plan.getByRole("button", { name: "Reload" }).click();
  await approve.click();
  await plan.waitFor({ state: "detached" });
  assert.equal(api.posts.length, 2);
  await ctx.close();
});

test("@agents Deny sends the hash and a single press", async () => {
  const api = agentApi();
  const { ctx, page } = await open({ setup: api.setup });
  await goAgents(page);
  await page.getByRole("button", { name: /migrate the billing tables/ }).click();
  await card(page, "plan").getByRole("button", { name: "Deny" }).click();
  await new Promise((res) => setTimeout(res, 200));
  assert.equal(api.posts.length, 1);
  assert.equal(api.posts[0].path, `/api/agent-jobs/${J1}/approvals/ap_plan/deny`);
  assert.equal(api.posts[0].body.hash, "h".repeat(64));
  await ctx.close();
});

test("@agents egress needs a second press with focus move; Esc reverts and never decides", async () => {
  const api = agentApi();
  const { ctx, page } = await open({ setup: api.setup });
  await goAgents(page);
  await page.getByRole("button", { name: /migrate the billing tables/ }).click();
  const egress = card(page, "egress");
  await egress.waitFor();
  await egress.getByRole("button", { name: "Approve", exact: true }).click();
  const confirm = egress.getByRole("button", { name: "Confirm approve egress" });
  await confirm.waitFor();
  assert.ok(await confirm.evaluate((el) => el === document.activeElement), "confirm takes focus");
  assert.equal(api.posts.length, 0, "a single click sends nothing");
  await page.keyboard.press("Escape");
  await egress.getByRole("button", { name: "Approve", exact: true }).waitFor();
  assert.ok(await egress.getByRole("button", { name: "Approve", exact: true }).evaluate((el) => el === document.activeElement), "focus returns");
  assert.equal(api.posts.length, 0, "Esc never decides");

  // Keyboard-only: Tab reaches Approve then Deny; Enter twice grants.
  await egress.getByRole("button", { name: "Approve", exact: true }).focus();
  await page.keyboard.press("Tab");
  assert.equal(await page.evaluate(() => document.activeElement?.textContent), "Deny");
  await page.keyboard.press("Shift+Tab");
  await page.keyboard.press("Enter");
  await confirm.waitFor();
  await page.keyboard.press("Enter");
  await new Promise((res) => setTimeout(res, 300));
  assert.equal(api.posts.length, 1);
  assert.equal(api.posts[0].path, `/api/agent-jobs/${J1}/approvals/ap_egr/approve`);
  assert.equal(api.posts[0].body.hash, "e".repeat(64));
  await ctx.close();
});

test("@agents expired approval renders denied and is not actionable", async () => {
  const api = agentApi({
    detail: () => json(200, DETAIL([{ ...APPROVALS()[1], expires_at: Date.now() / 1000 - 5 }])),
  });
  const { ctx, page } = await open({ setup: api.setup });
  await goAgents(page);
  await page.getByRole("button", { name: /migrate the billing tables/ }).click();
  const egress = card(page, "egress");
  await egress.getByText("Expired, denied").waitFor();
  assert.equal(await egress.getByRole("button").count(), 0);
  await ctx.close();
});

test("@agents submit: first POST shows the confirm step with the $25 default, confirm posts confirm=1; 422 shows inline", async () => {
  let reject = true;
  const api = agentApi({
    submit: (b) => {
      if (reject) return json(422, { error: "goal rejected by policy" });
      return b.confirm === "1" ? json(200, { job: J2 }) : json(200, { needs_confirm: true, budget: 25, model: "deepseek/deepseek-v4-pro" });
    },
  });
  const { ctx, page } = await open({ setup: api.setup });
  await goAgents(page);
  await page.getByText("Default $25 per working day.").waitFor();
  await page.getByLabel("New job").fill("do the long thing");
  await page.getByRole("button", { name: "Submit" }).click();
  await page.getByRole("alert").filter({ hasText: "goal rejected by policy" }).waitFor();
  assert.equal(api.posts[0].body.confirm, undefined);
  reject = false;
  await page.getByRole("button", { name: "Submit" }).click();
  await page.getByRole("heading", { name: "Start this job?" }).waitFor();
  assert.ok(await page.getByText("Budget $25").isVisible());
  assert.ok(await page.getByText("deepseek/deepseek-v4-pro").isVisible());
  await page.getByRole("button", { name: "Start job" }).click();
  await page.getByRole("heading", { name: "Start this job?" }).waitFor({ state: "detached" });
  const last = api.posts.at(-1);
  assert.equal(last.path, "/api/agent-jobs");
  assert.equal(last.body.confirm, "1");
  assert.equal(last.body.goal, "do the long thing");
  assert.ok(last.headers["x-csrf-token"]);
  await ctx.close();
});

test("@errors agent jobs 413 shows inline on the form", async () => {
  const api = agentApi({ submit: () => json(413, { error: "goal too large" }) });
  const { ctx, page } = await open({ setup: api.setup });
  await goAgents(page);
  await page.getByLabel("New job").fill("x");
  await page.getByRole("button", { name: "Submit" }).click();
  await page.getByRole("alert").filter({ hasText: "goal too large" }).waitFor();
  await ctx.close();
});

test("@errors agent jobs session expiry shows the auth banner, not a generic error", async () => {
  const api = agentApi({ list: () => json(401, { error: "Sign in again." }) });
  const { ctx, page } = await open({ setup: api.setup });
  await goAgents(page);
  await page.getByRole("status").filter({ hasText: "Your session expired" }).waitFor();
  assert.equal(await page.getByText("Sign in again.", { exact: true }).count(), 0);
  await ctx.close();
});

for (const scheme of ["light", "dark"]) {
  for (const w of [1440, 390]) {
    test(`@a11y agent jobs axe: 0 violations at ${w}px, ${scheme}`, async () => {
      const api = agentApi();
      const { ctx, page } = await open({ w, scheme, setup: api.setup });
      await goAgents(page);
      await page.getByRole("button", { name: /migrate the billing tables/ }).click();
      const egress = card(page, "egress");
      await egress.waitFor();
      await egress.getByRole("button", { name: "Approve", exact: true }).click(); // armed state too
      await egress.getByRole("button", { name: "Confirm approve egress" }).waitFor();
      await page.evaluate(AXE);
      const r = await page.evaluate(() => axe.run(document, { runOnly: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] }));
      assert.deepEqual(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(" | ")}`), [], `${w} ${scheme}`);
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
      await ctx.close();
    });
  }
}

// ---------------------------------------------------------------------------
// Effort composer: one goal box, a Low/Medium/High pick that chooses the model and the
// per-run budget, and every other knob behind a single Advanced disclosure. /api/effort is
// the real fixture route unless a test overrides it; every POST /goals is intercepted.

const EFFORT = {
  default: "low",
  max_budget: 1,
  models: ["google/gemini-2.5-flash", "deepseek/deepseek-v4-pro", "x-ai/grok-4.7"],
  levels: [
    { id: "low", label: "Low", blurb: "Quick and cheap.", model: "google/gemini-2.5-flash", budget: 0.1 },
    { id: "medium", label: "Medium", blurb: "Harder tasks.", model: "deepseek/deepseek-v4-pro", budget: 0.3 },
    { id: "high", label: "High", blurb: "Most capable allowed.", model: "x-ai/grok-4.7", budget: 1 },
  ],
};

function goalApi() {
  const posts = [];
  return {
    posts,
    routes: {
      "**/api/effort": (r) => r.fulfill(json(200, EFFORT)),
      "**/goals": (r) => {
        const body = JSON.parse(r.request().postData() || "{}");
        posts.push(body);
        if (body.confirm !== "1") {
          const lv = EFFORT.levels.find((l) => l.id === (body.effort ?? "low"));
          return r.fulfill(json(200, { needs_confirm: true, cap: 1, effort: lv.id, model: body.model ?? lv.model, budget: body.budget ?? lv.budget }));
        }
        return r.fulfill(json(200, { run: "g_e2e01" }));
      },
      "**/api/runs/g_e2e01": (r) => r.fulfill(json(200, RUN)),
    },
  };
}

const goalBox = (page) => page.getByRole("textbox", { name: "Describe a goal" });
const runBtn = (page) => page.getByRole("button", { name: "Run", exact: true });

test("@flow effort: server default is selected, Advanced is closed, and the caption names the model and budget", async () => {
  const api = goalApi();
  const { ctx, page } = await open({ routes: api.routes });
  await page.getByRole("radio", { name: "Low" }).waitFor({ state: "attached" });
  assert.ok(await page.getByRole("radio", { name: "Low" }).isChecked());
  assert.equal(await page.getByRole("radio").count(), 3);
  assert.equal(await page.getByRole("button", { name: "Advanced" }).getAttribute("aria-expanded"), "false");
  assert.equal(await page.getByLabel("Model").count(), 0);
  await page.getByText("up to $0.10 per run").waitFor();
  assert.ok(await page.getByText("google/gemini-2.5-flash").first().isVisible());
  await ctx.close();
});

test("@flow effort: High is sent with the goal, shown on the confirm card, and kept when confirming", async () => {
  const api = goalApi();
  const { ctx, page } = await open({ routes: api.routes });
  await goalBox(page).fill("say hi");
  await page.locator("label", { hasText: "High" }).click(); // the visible label, as a user clicks it
  await page.getByText("up to $1.00 per run").waitFor();
  await runBtn(page).click();
  const card = page.getByRole("heading", { name: "Run this?" }).locator("xpath=..");
  await card.waitFor();
  assert.deepEqual(api.posts[0], { goal: "say hi", effort: "high" }); // no model or budget unless Advanced was used
  const text = await card.innerText();
  assert.match(text, /high/i);
  assert.match(text, /x-ai\/grok-4\.7/);
  assert.match(text, /up to \$1\.00 for this run/);
  await page.getByRole("button", { name: "Run it" }).click();
  await page.getByText("<$0.0001").first().waitFor();
  assert.deepEqual(api.posts[1], { goal: "say hi", effort: "high", confirm: "1" });
  await ctx.close();
});

test("@flow effort: keyboard only: arrow keys change the level and Enter in the box submits it", async () => {
  const api = goalApi();
  const { ctx, page } = await open({ routes: api.routes });
  await page.getByRole("radio", { name: "Low" }).focus();
  await page.keyboard.press("ArrowRight");
  assert.ok(await page.getByRole("radio", { name: "Medium" }).isChecked());
  await goalBox(page).fill("say hi");
  await goalBox(page).press("Enter");
  await page.getByRole("heading", { name: "Run this?" }).waitFor();
  assert.equal(api.posts[0].effort, "medium");
  await ctx.close();
});

test("@flow effort: Advanced holds a model and a budget; both are sent, and an over-cap budget blocks Run", async () => {
  const api = goalApi();
  const { ctx, page } = await open({ routes: api.routes });
  await goalBox(page).fill("say hi");
  const adv = page.getByRole("button", { name: "Advanced" });
  await adv.click();
  assert.equal(await adv.getAttribute("aria-expanded"), "true");
  await page.getByLabel("Budget for this run, dollars").fill("5");
  await page.getByRole("alert").filter({ hasText: "up to $1.00" }).waitFor();
  assert.ok(await runBtn(page).isDisabled(), "a budget above the daily cap cannot be submitted");
  await page.getByLabel("Budget for this run, dollars").fill("0.25");
  await page.getByLabel("Model").selectOption("deepseek/deepseek-v4-pro");
  await adv.click(); // collapsed, the override is still in force and says so
  await page.getByText("· changed").waitFor();
  await runBtn(page).click();
  await page.getByRole("heading", { name: "Run this?" }).waitFor();
  assert.deepEqual(api.posts[0], { goal: "say hi", effort: "low", model: "deepseek/deepseek-v4-pro", budget: 0.25 });
  await ctx.close();
});

test("@errors effort: if the effort list cannot load, Run still works with the server default and says so", async () => {
  const api = goalApi();
  const { ctx, page } = await open({ routes: { ...api.routes, "**/api/effort": (r) => r.fulfill(json(500, { error: "boom" })) } });
  await page.getByText("Effort levels unavailable").waitFor();
  assert.equal(await page.getByRole("radio").count(), 0);
  await goalBox(page).fill("say hi");
  await runBtn(page).click();
  await page.getByRole("heading", { name: "Run this?" }).waitFor();
  assert.deepEqual(api.posts[0], { goal: "say hi" });
  await ctx.close();
});

test("@flow effort: a run shows its effort, model and spend, and a budget pause is stated, not silent", async () => {
  const withEffort = (effort) => ({ ...RUN, effort });
  for (const [effort, paused] of [
    [{ id: "medium", model: "deepseek/deepseek-v4-pro", budget: 0.3, spent: 0.0123, paused: false }, false],
    [{ id: "low", model: "google/gemini-2.5-flash", budget: 0.1, spent: 0.1, paused: true }, true],
  ]) {
    const { ctx, page } = await open({
      routes: {
        "**/api/runs": (r) => r.fulfill(json(200, { runs: [{ run: "g_e2e01", goal: "say hi", origin: "console", device_id: null, subtasks: 1, states: {}, ts: 1 }] })),
        "**/api/runs/g_e2e01": (r) => r.fulfill(json(200, withEffort(effort))),
      },
    });
    await page.getByText(`effort ${effort.id} · ${effort.model}`).waitFor();
    assert.equal(await page.getByRole("status").filter({ hasText: "Paused · budget" }).count(), paused ? 1 : 0);
    if (paused) assert.ok((await page.getByRole("status").filter({ hasText: "Nothing was cancelled" }).innerText()).includes("$0.10"));
    await ctx.close();
  }
});

for (const scheme of ["light", "dark"]) {
  for (const w of [1440, 390]) {
    test(`@a11y effort composer axe: 0 violations at ${w}px, ${scheme}, closed and with Advanced open`, async () => {
      const api = goalApi();
      const { ctx, page } = await open({ w, scheme, routes: api.routes });
      await page.getByRole("radio", { name: "Low" }).waitFor({ state: "attached" });
      const scan = async (label) => {
        await page.evaluate(AXE);
        const r = await page.evaluate(() => axe.run(document, { runOnly: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] }));
        assert.deepEqual(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(" | ")}`), [], `${w} ${scheme} ${label}`);
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), `no horizontal scroll ${label}`);
      };
      await scan("closed");
      await page.getByRole("button", { name: "Advanced" }).click();
      await scan("advanced open");
      await page.getByLabel("Budget for this run, dollars").fill("9"); // error state
      await scan("budget error");
      await ctx.close();
    });
  }
}

// ---------------------------------------------------------------------------
// Approvals card: who asked, what leaves, where to, cost, PII risk and the payload hash,
// plus the j/k/a/r chords. /api/approvals is intercepted so the data is deterministic.
// ---------------------------------------------------------------------------
const AP_NOW = () => Date.now() / 1000;
const APPROVAL_ITEMS = () => [
  { id: "ap_aaaa1111", ts: AP_NOW() - 120, age_s: 120, status: "pending", who: "docs-agent", session: "sess-4f21",
    what: "Summarise the v0.3 changelog", model: "gpt-4o-mini", egress: "api.openai.com", est_cost: 0.0042, pii: [],
    payload_hash: "a".repeat(64), payload_bytes: 321, payload: { prompt: "changelog text", egress: "api.openai.com" } },
  { id: "ap_bbbb2222", ts: AP_NOW() - 60, age_s: 60, status: "pending", who: "research-agent", session: "",
    what: "Draft the customer reply", model: "claude", egress: "openrouter.ai", est_cost: 0.07, pii: ["email", "ssn"],
    payload_hash: "b".repeat(64), payload_bytes: 99, payload: { prompt: "reply to jo@example.com" } },
  { id: "ap_cccc3333", ts: AP_NOW() - 30, age_s: 30, status: "pending", who: "deploy-agent", what: "Plan the rollout",
    model: "m", egress: "api.example.com", est_cost: 0, pii: [], payload_hash: "c".repeat(64), payload_bytes: 10, payload: {} },
];
function approvalsApi(items = APPROVAL_ITEMS()) {
  const posts = [];
  return {
    posts,
    routes: {
      "**/api/approvals": (r) => r.fulfill(json(200, { approvals: items.filter((i) => !posts.some((p) => p.id === i.id)).map((i) => i) })),
      "**/api/approvals/*/*": async (r) => {
        const m = new URL(r.request().url()).pathname.match(/approvals\/([^/]+)\/([^/]+)$/);
        posts.push({ id: m[1], verb: m[2], body: r.request().postDataJSON() });
        await r.fulfill(json(200, { ok: true }));
      },
    },
  };
}

test("@approvals card shows who asked, what leaves, where to, cost, PII risk and the payload hash", async () => {
  const { ctx, page } = await openAt("/approvals", { routes: approvalsApi().routes });
  const cards = page.locator("li[data-approval]");
  await cards.first().waitFor();
  assert.equal(await cards.count(), 3);
  const a = cards.nth(0), b = cards.nth(1);
  const textA = await a.innerText();
  for (const needle of ["Summarise the v0.3 changelog", "docs-agent", "sess-4f21", "api.openai.com", "$0.0042", "Low risk", "No PII", "aaaaaaaaaaaa", "321 bytes"]) assert.ok(textA.includes(needle), `card A shows ${needle}`);
  assert.ok(!textA.includes("Approval request"), "no generic placeholder title");
  const textB = await b.innerText();
  for (const needle of ["research-agent", "openrouter.ai", "$0.07", "High risk", "PII: email, ssn", "bbbbbbbbbbbb"]) assert.ok(textB.includes(needle), `card B shows ${needle}`);
  await b.getByText("Show the payload").click();
  assert.match(await b.getByLabel("Payload for ap_bbbb2222").innerText(), /jo@example\.com/);
  await ctx.close();
});

test("@approvals @keyboard j/k move, a approves the selected card with its hash, r focuses the note, ctrl+enter sends back", async () => {
  const api = approvalsApi();
  const { ctx, page } = await openAt("/approvals", { routes: api.routes });
  const cards = page.locator("li[data-approval]");
  await cards.first().waitFor();
  const current = () => page.locator("li[aria-current=true]").getAttribute("data-approval");
  assert.equal(await current(), "ap_aaaa1111");
  await page.keyboard.press("j");
  assert.equal(await current(), "ap_bbbb2222");
  await page.keyboard.press("j"); await page.keyboard.press("j"); // clamps at the last card
  assert.equal(await current(), "ap_cccc3333");
  await page.keyboard.press("k");
  assert.equal(await current(), "ap_bbbb2222");
  // r focuses this card's note; typing letters there must not trigger chords
  await page.keyboard.press("r");
  assert.equal(await page.evaluate(() => document.activeElement?.getAttribute("aria-label")), "Note for send-back on ap_bbbb2222");
  await page.keyboard.type("jak Redact the SSN");
  assert.equal(await current(), "ap_bbbb2222");
  assert.equal(api.posts.length, 0);
  await page.keyboard.press("Control+Enter");
  await cards.nth(2).waitFor({ state: "detached" });
  assert.deepEqual(api.posts, [{ id: "ap_bbbb2222", verb: "send-back", body: { hash: "b".repeat(64), note: "jak Redact the SSN" } }]);
  assert.equal(await cards.count(), 2);
  // focus left the removed input; `a` approves the selected card, bound to that card's hash
  // selection stays on the same slot, so the next card is selected; k goes back to the first
  await page.locator("body").click({ position: { x: 5, y: 5 } });
  assert.equal(await current(), "ap_cccc3333");
  await page.keyboard.press("k");
  assert.equal(await current(), "ap_aaaa1111");
  await page.keyboard.press("a");
  await page.waitForFunction(() => document.querySelectorAll("li[data-approval]").length === 1);
  assert.equal(api.posts[1].id, "ap_aaaa1111");
  assert.equal(api.posts[1].verb, "approve");
  assert.equal(api.posts[1].body.hash, "a".repeat(64));
  await ctx.close();
});

for (const w of [1440, 390]) {
  test(`@a11y approvals card with PII risk and payload open: axe 0 violations, no horizontal scroll at ${w}px`, async () => {
    const { ctx, page } = await openAt("/approvals", { w, routes: approvalsApi().routes });
    await page.locator("li[data-approval]").first().waitFor();
    await page.locator("li[data-approval]").nth(1).getByText("Show the payload").click();
    assert.deepEqual(await axeViolations(page), [], `approvals @${w}`);
    assert.ok(await noHScroll(page), `approvals @${w} scrolls horizontally`);
    await ctx.close();
  });
}
