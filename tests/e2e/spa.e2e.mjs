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
const PORT = process.env.OC_E2E_PORT || "8891"; // override when another checkout is testing on the same machine
const PY = (process.env.OC_PYTHON || "uv run --with fido2 python").split(" ");

if (!fs.existsSync(path.join(REPO, "ui/dist/index.html"))) {
  throw new Error("ui/dist not built — run `pnpm --dir ui build` first");
}

let browser, base, server;
before(async () => {
  browser = await chromium.launch();
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "oc-spa-"));
  server = spawn(PY[0], [...PY.slice(1), "-I", "tests/opsview/serve_fixture.py", PORT, dir], { cwd: REPO });
  server.stderr.on("data", (d) => process.stderr.write(d));
  await new Promise((res, rej) => {
    server.stdout.on("data", (d) => String(d).includes("ready") && res());
    server.on("exit", (c) => rej(new Error("fixture exited " + c)));
    setTimeout(() => rej(new Error("fixture timeout")), 30000);
  });
  base = `http://127.0.0.1:${PORT}`;
});
after(async () => { await browser?.close(); server?.kill(); });

const json = (status, body) => ({ status, contentType: "application/json", body: JSON.stringify(body) });

async function open({ w = 1440, h = 900, routes = {}, scheme = "light", setup, path = "/", motion = "reduce" } = {}) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, reducedMotion: motion, colorScheme: scheme });
  await ctx.addCookies([{ name: "oc_sess", value: "test", url: base }]);
  const page = await ctx.newPage();
  // Empty runs list by default so the composer (not a run) is the landing view.
  await page.route("**/api/runs*", (r) => r.fulfill(json(200, { runs: [] })));
  // The sidebar reads /api/dev; the fixture server predates it, so answer it here (tests override).
  await page.route("**/api/dev", (r) => r.fulfill(json(200, { trace: false, env: false, active: false })));
  await page.route("**/api/runs/*/events*", (r) => r.fulfill(json(404, { error: "dev trace off for this run" })));
  // Fake run ids: an unrouted files request reaches the fixture server and can stay pending, starving networkidle.
  await page.route("**/api/runs/*/artifacts", (r) => r.fulfill(json(200, { artifacts: [] })));
  for (const [glob, handler] of Object.entries(routes)) await page.route(glob, handler);
  if (setup) await setup(page);
  await page.goto(base + path, { waitUntil: "networkidle" });
  return { ctx, page };
}

const RUN = {
  run: "g_e2e01", goal: "say hi", origin: "console", device_id: null, declared: 1, worker_failures: [],
  subtasks: [{ subtask: "s1", title: "say hi", state: "DONE", route: "cheap", engine: "jev", placement: "cloud-a",
    reason: "", gate_required: false, cost: 0.000026, pii: [], result: "hi", ts: 1, finished_ts: 2 }],
};

for (const w of [1440, 390]) {
  test(`@a11y spa axe: 0 violations at ${w}px`, async () => {
    const { ctx, page } = await open({ w });
    await page.getByRole("textbox", { name: "Message" }).waitFor();
    await page.evaluate(AXE);
    const r = await page.evaluate(() => axe.run(document, { runOnly: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] }));
    assert.deepEqual(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(" | ")}`), [], `${w}`);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    await ctx.close();
  });
}

const THREAD = (over = {}) => ({
  id: "th_e2e00001", title: "say hi", created: 1, updated: 2, archived: false, working: false,
  effort: { effort: "low", model: "google/gemini-2.5-flash", budget: 0.1 },
  messages: [
    { role: "user", text: "say hi", ts: 1 },
    { role: "assistant", text: "Sending a greeting.", ts: 2 },
    { role: "run", text: "say hi", run: "g_e2e01", goal: "say hi", ts: 3 },
    { role: "assistant", text: "Done: it said hi.", ts: 4 },
  ],
  runs: { g_e2e01: { ...RUN, run: "g_e2e01" } },
  ...over,
});

test("@flow chat: double Send opens one chat, a failed send keeps the text, retry lands in the thread with the run inline", async () => {
  let posts = 0;
  let fail = true;
  const { ctx, page } = await open({
    routes: {
      "**/api/threads*": async (r) => {
        if (new URL(r.request().url()).pathname !== "/api/threads") return r.fallback();
        if (r.request().method() !== "POST") return r.fulfill(json(200, { threads: [] }));
        posts++;
        await new Promise((res) => setTimeout(res, 300)); // window for a double click
        if (fail) return r.fulfill(json(500, { error: "orchestrator down" }));
        return r.fulfill(json(200, { thread: "th_e2e00001" }));
      },
      "**/api/threads/th_e2e00001": (r) => r.fulfill(json(200, THREAD())),
    },
  });
  const box = page.getByRole("textbox", { name: "Message" });
  await box.fill("say hi");
  const send = page.getByRole("button", { name: /^Send/ }); // the label reads "Sending…" while pending
  await send.dispatchEvent("click");
  await send.dispatchEvent("click"); // a second press while the first is pending
  await page.getByRole("alert").filter({ hasText: "orchestrator down" }).waitFor();
  assert.equal(posts, 1, "pending send must block a second request");
  assert.equal(await box.inputValue(), "say hi", "the text survives a failed send");

  fail = false;
  await send.click();
  await page.getByText("Done: it said hi.").waitFor();
  assert.ok(await page.getByText("Sending a greeting.").isVisible());
  const conv = page.getByRole("list", { name: "Conversation" });
  assert.ok(await conv.getByRole("link", { name: "Details" }).first().isVisible(), "the run renders inline");
  assert.equal(await conv.getByText("<$0.0001").count(), 0, "a sub-cent cost stays off the chat card");
  await ctx.close();
});

test("@errors unreachable API shows the connection banner", async () => {
  const { ctx, page } = await open({ routes: { "**/api/threads*": (r) => r.abort("connectionrefused") } });
  await page.getByRole("status").filter({ hasText: "Connection lost" }).waitFor();
  await page.getByRole("textbox", { name: "Message" }).fill("anything");
  assert.ok(await page.getByRole("button", { name: "Send", exact: true }).isDisabled(), "offline disables send; never queues");
  await ctx.close();
});

test("@errors expired session shows sign-in and disables the composer", async () => {
  const { ctx, page } = await open({ routes: { "**/api/session": (r) => r.fulfill(json(401, { error: "Sign in again." })) } });
  await page.getByRole("status").filter({ hasText: "Your session expired" }).waitFor();
  await page.getByRole("textbox", { name: "Message" }).fill("anything");
  assert.ok(await page.getByRole("button", { name: "Send", exact: true }).isDisabled());
  await ctx.close();
});

test("@chat Operator stays collapsed by default and has no fake Attachments/Projects/Teammates chrome", async () => {
  const { ctx, page } = await open();
  assert.equal(await page.getByRole("navigation", { name: "Operator" }).count(), 0);
  await page.getByText("Operator", { exact: true }).click();
  await page.getByRole("navigation", { name: "Operator" }).waitFor();
  assert.equal(await page.getByRole("button", { name: /Attachments|Projects|Teammates|Customize|Search/ }).count(), 0);
  assert.equal(await page.getByRole("radio", { name: /Search|Computer/ }).count(), 0);
  await ctx.close();
});

test("@chat dictation unavailable before recording when voice is not configured", async () => {
  const { ctx, page } = await open({
    routes: { "**/api/voice": (r) => r.fulfill(json(200, { available: false, reason: "voice not configured" })) },
  });
  await page.getByRole("button", { name: "Dictate" }).click();
  await page.getByRole("alert").filter({ hasText: /Dictation isn’t available|Dictation isn't available/ }).waitFor();
  assert.equal(await page.getByRole("button", { name: "Dictate" }).getAttribute("aria-label"), "Dictate");
  await ctx.close();
});

test("@chat awaiting-approval run shows Open Approvals from the inline run card", async () => {
  const run = {
    ...RUN,
    subtasks: [
      {
        ...RUN.subtasks[0],
        state: "AWAITING-APPROVAL",
        approval: "ap_e2e01",
        finished_ts: null,
      },
    ],
  };
  const { ctx, page } = await open({
    routes: {
      "**/api/threads": (r) => r.fulfill(json(200, { threads: [{ id: "th_e2e00001", title: "say hi", ts: 1, archived: false, working: true, finished: false }] })),
      "**/api/threads/th_e2e00001": (r) =>
        r.fulfill(json(200, THREAD({ working: true, runs: { g_e2e01: run }, messages: THREAD().messages.slice(0, 3) }))),
      "**/api/runs/g_e2e01": (r) => r.fulfill(json(200, run)),
    },
    path: "/#th_e2e00001",
  });
  await page.getByRole("status").filter({ hasText: "Needs approval" }).waitFor();
  assert.ok(await page.getByRole("link", { name: "Open Approvals" }).isVisible());
  await ctx.close();
});

test("@flow memory: a reply shows the notes its turn used, Forget posts once and marks the note", async () => {
  const posts = [];
  const memory = {
    recalls: [{ user_ts: 1, ts: 1.5, withheld: 1, atoms: [{ id: "atom_e2e1", title: "Deploy notes", source: "agent", score: 0.8 }] }],
    writes: [],
  };
  const { ctx, page } = await open({
    routes: {
      "**/api/threads": (r) => r.fulfill(json(200, { threads: [{ id: "th_e2e00001", title: "say hi", ts: 1, archived: false, working: false, finished: true }] })),
      "**/api/threads/th_e2e00001": (r) => r.fulfill(json(200, THREAD({ memory }))),
      "**/api/runs/g_e2e01": (r) => r.fulfill(json(200, RUN)),
      "**/api/memory/forget": (r) => {
        posts.push(JSON.parse(r.request().postData() || "{}"));
        return r.fulfill(json(200, { ok: true, atom: "atom_e2e1" }));
      },
    },
    path: "/#th_e2e00001",
  });
  const chip = page.getByRole("button", { name: "Used 1 memory" });
  await chip.waitFor();
  assert.equal(await page.getByTestId("memory-chip").count(), 1, "one chip, on the first reply after the message");
  await chip.click();
  await page.getByText("Deploy notes").waitFor();
  assert.ok(await page.getByText(/1 note held back by the privacy check/).isVisible());
  await page.evaluate(AXE);
  const r = await page.evaluate(() => axe.run(document, { runOnly: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] }));
  assert.deepEqual(r.violations.map((v) => v.id), []);
  await page.getByRole("button", { name: "Forget Deploy notes" }).click();
  await page.getByText("Forgotten", { exact: true }).waitFor();
  assert.deepEqual(posts, [{ atom: "atom_e2e1", thread: "th_e2e00001" }]);
  await ctx.close();
});

test("@flow memory: with memory off a thread shows no chip and Settings says how to turn it on", async () => {
  const { ctx, page } = await open({
    routes: {
      "**/api/threads": (r) => r.fulfill(json(200, { threads: [] })),
      "**/api/threads/th_e2e00001": (r) => r.fulfill(json(200, THREAD())),
      "**/api/runs/g_e2e01": (r) => r.fulfill(json(200, RUN)),
      "**/api/memory": (r) => r.fulfill(json(200, { enabled: false, project: "open-computer", counts: { recalls: 0, writes: 0, withheld: 0, forgotten: 0, errors: 0 }, last_error: null, recent_writes: [] })),
    },
    path: "/#th_e2e00001",
  });
  await page.getByText("Done: it said hi.").waitFor();
  assert.equal(await page.getByTestId("memory-chip").count(), 0);
  await page.goto(base + "/settings", { waitUntil: "networkidle" });
  await page.getByTestId("memory-status").filter({ hasText: "Off. Set OC_MEMORY=kurultai" }).waitFor();
  await ctx.close();
});

test("@flow memory: Settings shows the endpoint, counts and a failure, and forgets a stored note", async () => {
  let forgotten = false;
  const posts = [];
  const status = () => ({
    enabled: true, project: "open-computer", url: "http://127.0.0.1:8421/mcp", limit: 5, include_quarantine: false, token: false,
    counts: { recalls: 4, writes: 2, withheld: 1, forgotten: forgotten ? 1 : 0, errors: 1 },
    last_error: { ts: Date.now() / 1000 - 120, op: "recall" },
    recent_writes: forgotten ? [] : [{ atom: "atom_e2e9", title: "Weekly report", thread: "th_e2e00001", ts: Date.now() / 1000 - 60 }],
  });
  const { ctx, page } = await open({
    path: "/settings",
    routes: {
      "**/api/memory": (r) => r.fulfill(json(200, status())),
      "**/api/memory/forget": (r) => {
        posts.push(JSON.parse(r.request().postData() || "{}"));
        forgotten = true;
        return r.fulfill(json(200, { ok: true, atom: "atom_e2e9" }));
      },
    },
  });
  await page.getByTestId("memory-status").filter({ hasText: "On. Each message recalls up to 5 notes" }).waitFor();
  assert.ok(await page.getByText("http://127.0.0.1:8421/mcp").isVisible());
  assert.ok(await page.getByText("None: Kurultai may refuse calls (OC_MEMORY_TOKEN)").isVisible());
  assert.ok(await page.getByText(/recall 2m ago; chats carried on without memory/).isVisible());
  await page.getByRole("button", { name: "Forget Weekly report" }).click();
  await page.getByTestId("memory-counts").filter({ hasText: "1 forgotten" }).waitFor();
  assert.deepEqual(posts, [{ atom: "atom_e2e9" }]);
  assert.equal(await page.getByRole("list", { name: "Stored memories" }).count(), 0);
  await ctx.close();
});

test("@errors a missing run offers retry instead of spinning forever", async () => {
  const { ctx, page } = await open({
    routes: {
      "**/api/runs*": (r) => r.fulfill(json(200, { runs: [{ run: "g_gone", goal: "x", origin: "console", device_id: null, subtasks: 1, states: {}, ts: 1 }] })),
      "**/api/runs/g_gone": (r) => r.fulfill(json(404, { error: "no such run" })),
    },
    path: "/#g_gone",
  });
  await page.getByRole("alert").filter({ hasText: "no longer exists" }).waitFor();
  await page.getByRole("main").getByRole("button", { name: "New chat", exact: true }).click();
  await page.getByRole("textbox", { name: "Message" }).waitFor();
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
  await page.route("**/api/dev", (r) => r.fulfill(json(200, { trace: false, env: false, active: false })));
  await page.route("**/api/runs/*/events*", (r) => r.fulfill(json(404, { error: "dev trace off for this run" })));
  // Fake run ids: an unrouted files request reaches the fixture server and can stay pending, starving networkidle.
  await page.route("**/api/runs/*/artifacts", (r) => r.fulfill(json(200, { artifacts: [] })));
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
    { device_id: "dev-host", os: "linux-aarch64", capabilities: ["browser", "fs"], roots: ["/home/user/src"], online: true, awake: true, eligible: true, state: "online", last_heartbeat: Date.now() / 1000 - 30, active_grants: 1, inbox_depth: 0 },
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
  assert.deepEqual(entries.map(([t]) => t), ["Chat", "Work", "Overview", "Goals", "Automations", "Skills", "Sandboxes", "Devices", "Runs", "Approvals", "Router", "Audit", "Workspace", "Settings"]);
  for (const href of ["/", "/devices", "/sandboxes", "/router", "/audit", "/settings"]) {
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
  await page.getByRole("textbox", { name: "Message" }).waitFor();
  await ctx.close();
});

test("devices: rows, states as words, empty fixture, and hostile text stays text", async () => {
  let { ctx, page } = await openAt("/devices", { routes: { "**/api/devices": (r) => r.fulfill(json(200, DEVICES)) } });
  await page.getByRole("table", { name: "Devices, state and queue depth" }).waitFor();
  await page.getByText("linux-aarch64").waitFor();
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
  await page.getByText("Operator", { exact: true }).click(); // the operator links sit in a closed disclosure
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
    { id: "low", label: "Low", blurb: "Quick and cheap.", model: "google/gemini-2.5-flash", fallbacks: ["vendor/low-b", "vendor/low-c"], budget: 0.1 },
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
      "**/api/threads*": (r) => {
        if (new URL(r.request().url()).pathname !== "/api/threads") return r.fallback();
        if (r.request().method() !== "POST") return r.fulfill(json(200, { threads: [] }));
        posts.push(JSON.parse(r.request().postData() || "{}"));
        return r.fulfill(json(200, { thread: "th_e2e00001" }));
      },
      "**/api/threads/th_e2e00001": (r) => r.fulfill(json(200, THREAD())),
    },
  };
}

const goalBox = (page) => page.getByRole("textbox", { name: "Message" });
const runBtn = (page) => page.getByRole("button", { name: "Send", exact: true });

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
  assert.ok(await page.getByText("vendor/low-b, vendor/low-c").isVisible());
  await ctx.close();
});

test("@flow effort: High is sent with the first message and there is no confirm step", async () => {
  const api = goalApi();
  const { ctx, page } = await open({ routes: api.routes });
  await goalBox(page).fill("say hi");
  await page.locator("label", { hasText: "High" }).click(); // the visible label, as a user clicks it
  await page.getByText("up to $1.00 per run").waitFor();
  await runBtn(page).click();
  await page.getByText("Done: it said hi.").waitFor(); // straight into the thread
  assert.deepEqual(api.posts, [{ text: "say hi", effort: "high" }]); // no model or budget unless Advanced was used
  assert.equal(await page.getByRole("heading", { name: "Run this?" }).count(), 0);
  assert.equal(await page.getByRole("radio").count(), 0, "inside a thread the effort picker is gone");
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
  await page.getByText("Done: it said hi.").waitFor();
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
  await page.getByText("Done: it said hi.").waitFor();
  assert.deepEqual(api.posts[0], { text: "say hi", effort: "low", model: "deepseek/deepseek-v4-pro", budget: 0.25 });
  await ctx.close();
});

test("@errors effort: if the effort list cannot load, Run still works with the server default and says so", async () => {
  const api = goalApi();
  const { ctx, page } = await open({ routes: { ...api.routes, "**/api/effort": (r) => r.fulfill(json(500, { error: "boom" })) } });
  await page.getByText("Effort levels unavailable").waitFor();
  assert.equal(await page.getByRole("radio").count(), 0);
  await goalBox(page).fill("say hi");
  await runBtn(page).click();
  await page.getByText("Done: it said hi.").waitFor();
  assert.deepEqual(api.posts[0], { text: "say hi" });
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
        "**/api/runs*": (r) => r.fulfill(json(200, { runs: [{ run: "g_e2e01", goal: "say hi", origin: "console", device_id: null, subtasks: 1, states: {}, ts: 1 }] })),
        "**/api/runs/g_e2e01": (r) => r.fulfill(json(200, withEffort(effort))),
      },
      path: "/#g_e2e01",
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

// ---- Silver Lining tokens (U19): one token file, dark and light, system by default.
const CANVAS = { dark: "rgb(9, 9, 9)", light: "rgb(245, 245, 245)" };
const canvasOf = (page) => page.evaluate(() => getComputedStyle(document.body).backgroundColor);

test("@theme system is the default and follows an emulated scheme change without reload", async () => {
  const { ctx, page } = await open({ scheme: "light" });
  assert.equal(await page.evaluate(() => document.documentElement.getAttribute("data-theme")), null);
  assert.equal(await canvasOf(page), CANVAS.light);
  await page.emulateMedia({ colorScheme: "dark" });
  assert.equal(await canvasOf(page), CANVAS.dark);
  await ctx.close();
});

test("@theme a stored dark choice wins over a light system and survives reload", async () => {
  const { ctx, page } = await open({ scheme: "light", setup: (p) => p.addInitScript(() => localStorage.setItem("oc-theme", "dark")) });
  assert.equal(await page.evaluate(() => document.documentElement.getAttribute("data-theme")), "dark");
  assert.equal(await canvasOf(page), CANVAS.dark);
  await page.reload({ waitUntil: "networkidle" });
  assert.equal(await canvasOf(page), CANVAS.dark);
  await ctx.close();
});

for (const scheme of ["dark", "light"]) {
  test(`@theme a server-rendered page and the SPA share the ${scheme} canvas`, async () => {
    const { ctx, page } = await open({ scheme });
    const spa = await canvasOf(page);
    await page.goto(base + "/runs/new", { waitUntil: "networkidle" });
    const server = await canvasOf(page);
    assert.equal(spa, CANVAS[scheme]);
    assert.equal(server, CANVAS[scheme]);
    await ctx.close();
  });
}

// ---- cleanup: delete, archive, clear finished (the API is faked in memory; nothing is removed for real)
const row = (run, goal, finished = true, archived = false) =>
  ({ run, goal, origin: "console", device_id: null, subtasks: 1, states: {}, ts: 1, finished, archived });

function cleanupApi(rows) {
  const calls = [];
  const routes = {
    "**/api/runs*": (r) => {
      const u = new URL(r.request().url());
      if (u.pathname !== "/api/runs") return r.fallback();
      return r.fulfill(json(200, { runs: rows.filter((x) => u.searchParams.get("archived") === "1" || !x.archived) }));
    },
    "**/api/runs/*/*": (r) => {
      const [, , , id, op] = new URL(r.request().url()).pathname.split("/");
      const body = JSON.parse(r.request().postData() || "{}");
      calls.push([op, id, body]);
      const i = rows.findIndex((x) => x.run === id);
      if (op === "delete") rows.splice(i, 1);
      if (op === "archive") rows[i].archived = body.archived;
      return r.fulfill(json(200, { ok: true }));
    },
    "**/api/runs/clear": (r) => {
      calls.push(["clear"]);
      const n = rows.filter((x) => x.finished).length;
      for (let i = rows.length - 1; i >= 0; i--) if (rows[i].finished) rows.splice(i, 1);
      return r.fulfill(json(200, { cleared: n }));
    },
  };
  return { calls, routes };
}

test("@cleanup (earlier runs) delete asks in place, then removes the run; a working run offers no delete", async () => {
  const rows = [row("goal_0000000a", "old junk"), row("goal_0000000b", "still going", false)];
  const { calls, routes } = cleanupApi(rows);
  const { ctx, page } = await open({ routes });
  await page.getByText("Earlier runs").click();
  const junk = page.locator("div.group", { hasText: "old junk" });
  await junk.hover();
  await junk.getByRole("button", { name: "Delete" }).click();
  await page.getByText("Delete this?").waitFor();
  await page.getByRole("button", { name: "Keep" }).click();
  assert.deepEqual(calls, [], "Keep must not call the API");
  await junk.hover();
  await junk.getByRole("button", { name: "Delete" }).click();
  await page.getByRole("button", { name: "Delete", exact: true }).click();
  await page.getByText("old junk").waitFor({ state: "detached" });
  assert.deepEqual(calls, [["delete", "goal_0000000a", {}]]);
  const busy = page.locator("div.group", { hasText: "still going" });
  await busy.hover();
  assert.equal(await busy.getByRole("button", { name: "Delete" }).count(), 0);
  await ctx.close();
});

test("@cleanup archive moves a run under Archived and restore brings it back", async () => {
  const rows = [row("goal_0000000a", "keep me around")];
  const { calls, routes } = cleanupApi(rows);
  const { ctx, page } = await open({ routes });
  await page.getByText("Earlier runs").click();
  const r = page.locator("div.group", { hasText: "keep me around" });
  await r.hover();
  await r.getByRole("button", { name: "Archive" }).click();
  await page.getByText("Archived (1)").waitFor();
  await page.getByText("Archived (1)").click();
  const a = page.locator("div.group", { hasText: "keep me around" });
  await a.hover();
  await a.getByRole("button", { name: "Restore" }).click();
  await page.getByText("Archived (1)").waitFor({ state: "detached" });
  assert.deepEqual(calls.map((c) => [c[0], c[2].archived]), [["archive", true], ["archive", false]]);
  await ctx.close();
});

test("@cleanup Clear finished confirms, removes only finished runs, keeps working ones", async () => {
  const rows = [row("goal_0000000a", "done one"), row("goal_0000000b", "done two"), row("goal_0000000c", "running one", false)];
  const { calls, routes } = cleanupApi(rows);
  const { ctx, page } = await open({ routes });
  await page.getByText("Earlier runs").click();
  await page.getByRole("button", { name: "Clear finished" }).click();
  await page.getByRole("button", { name: "Remove 2" }).click();
  await page.getByText("done one").waitFor({ state: "detached" });
  assert.deepEqual(calls, [["clear"]]);
  assert.ok(await page.getByText("running one").isVisible());
  assert.equal(await page.getByRole("button", { name: "Clear finished" }).count(), 0);
  await ctx.close();
});

for (const scheme of ["light", "dark"]) {
  test(`@cleanup rail hover state screenshot (${scheme})`, async () => {
    const rows = [row("goal_0000000a", "Summarise the audit log"), row("goal_0000000b", "Map the dataset", false), row("goal_0000000c", "Old experiment", true, true)];
    const { routes } = cleanupApi(rows);
    const { ctx, page } = await open({ routes, scheme, w: 1100, h: 640 });
    await page.getByText("Earlier runs").click();
    await page.locator("div.group", { hasText: "Summarise" }).hover();
    if (process.env.OC_SHOTS) await page.screenshot({ path: `${process.env.OC_SHOTS}/cleanup-${scheme}.png` });
    await ctx.close();
  });
}

// ---- chat: a thread continues, shows runs inline, and can be removed
function threadApi(state = {}) {
  const th = { ...THREAD(), ...state };
  const calls = [];
  const sum = () => ({ id: th.id, title: th.title, ts: 2, archived: th.archived, working: th.working, finished: !th.working });
  return {
    th, calls,
    routes: {
      "**/api/threads*": (r) => {
        const u = new URL(r.request().url());
        if (u.pathname === "/api/threads") return r.fulfill(json(200, { threads: th.deleted ? [] : [sum()] }));
        return r.fallback();
      },
      "**/api/threads/th_e2e00001": (r) => r.fulfill(th.deleted ? json(404, { error: "no such chat" }) : json(200, th)),
      "**/api/threads/th_e2e00001/*": (r) => {
        const op = new URL(r.request().url()).pathname.split("/").pop();
        const body = JSON.parse(r.request().postData() || "{}");
        calls.push([op, body]);
        if (op === "delete") th.deleted = true;
        if (op === "messages") { th.working = true; th.messages.push({ role: "user", text: body.text, ts: 5 }); }
        return r.fulfill(json(200, { ok: true }));
      },
    },
  };
}

test("@chat a follow-up continues the same thread: one POST to that thread, message shows at once, no effort picker", async () => {
  const api = threadApi();
  const { ctx, page } = await open({ routes: { ...api.routes, "**/api/effort": (r) => r.fulfill(json(200, EFFORT)) }, path: "/#th_e2e00001" });
  await page.getByText("Done: it said hi.").waitFor();
  assert.equal(await page.getByRole("radio").count(), 0);
  const box = page.getByRole("textbox", { name: "Message" });
  await box.fill("now make it louder");
  // The UI is optimistic: the message and "Working" render before the POST (which first fetches a CSRF
  // token) leaves the page, so wait for the POST's response before asserting what the server saw.
  const posted = page.waitForResponse((r) => r.request().method() === "POST" && r.url().endsWith("/api/threads/th_e2e00001/messages"));
  await box.press("Enter");
  await page.getByRole("list", { name: "Conversation" }).getByText("now make it louder").waitFor();
  await page.getByRole("status").filter({ hasText: "Working" }).waitFor();
  await posted;
  assert.deepEqual(api.calls, [["messages", { text: "now make it louder" }]]);
  await ctx.close();
});

test("@chat deleting a chat asks first and returns to a new chat", async () => {
  const api = threadApi();
  const { ctx, page } = await open({ routes: api.routes, path: "/#th_e2e00001" });
  await page.getByText("Done: it said hi.").waitFor();
  const row = page.locator("div.group", { hasText: "say hi" });
  await row.hover();
  await row.getByRole("button", { name: "Delete" }).click();
  await page.getByRole("button", { name: "Keep" }).click();
  assert.deepEqual(api.calls, []);
  await row.hover();
  await row.getByRole("button", { name: "Delete" }).click();
  await page.getByRole("button", { name: "Delete", exact: true }).click();
  await page.getByRole("heading", { name: "What are we working on?" }).waitFor();
  assert.deepEqual(api.calls, [["delete", {}]]);
  await ctx.close();
});

for (const scheme of ["light", "dark"]) {
  for (const w of [1440, 390]) {
    test(`@a11y chat axe: home and thread, 0 violations at ${w}px, ${scheme}`, async () => {
      const api = threadApi();
      const { ctx, page } = await open({ w, scheme, routes: { ...api.routes, "**/api/effort": (r) => r.fulfill(json(200, EFFORT)) } });
      await page.getByRole("heading", { name: "What are we working on?" }).waitFor();
      const scan = async (label) => {
        await page.evaluate(AXE);
        const r = await page.evaluate(() => axe.run(document, { runOnly: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] }));
        assert.deepEqual(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(" | ")}`), [], `${w} ${scheme} ${label}`);
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), `no horizontal scroll ${label}`);
        if (process.env.OC_SHOTS) await page.screenshot({ path: `${process.env.OC_SHOTS}/chat-${label}-${scheme}-${w}.png` });
      };
      await scan("home");
      await page.goto(base + "/#th_e2e00001");
      await page.getByText("Done: it said hi.").waitFor();
      await scan("thread");
      await ctx.close();
    });
  }
}

test("@flow run page: only steps with detected PII say private; an empty list and a legacy null say nothing", async () => {
  const step = (id, pii) => ({ ...RUN.subtasks[0], subtask: id, title: `step ${id}`, pii });
  const run = { ...RUN, declared: 3, subtasks: [step("a", []), step("b", ["email", "ssn"]), step("c", null)] };
  const { ctx, page } = await open({
    routes: {
      "**/api/runs*": (r) => r.fulfill(json(200, { runs: [{ run: "g_e2e01", goal: "say hi", origin: "console", device_id: null, subtasks: 3, states: {}, ts: 1 }] })),
      "**/api/runs/g_e2e01": (r) => r.fulfill(json(200, run)),
    },
    path: "/#g_e2e01",
  });
  await page.getByText("step b").waitFor();
  const label = page.getByText("private details");
  assert.equal(await label.count(), 1);
  assert.equal(await label.getAttribute("title"), "Detected: email, ssn");
  assert.ok(await page.locator("li", { hasText: "step b" }).getByText("private details").isVisible());
  assert.equal(await page.locator("li", { hasText: "step a" }).getByText("private details").count(), 0);
  assert.equal(await page.locator("li", { hasText: "step c" }).getByText("private details").count(), 0);
  await ctx.close();
});

// ---------------------------------------------------------------------------
// Run timeline (dev mode): events come from routed fixtures, never a live run.
// ---------------------------------------------------------------------------
import { GID, manySteps, stream, threeSteps } from "./timeline-fixture.mjs";

const TL = `/#${GID}/timeline`;
const PROBE = 2 ** 31 - 1;

function eventsRoute(getEvents, { afters = [], finished = () => true } = {}) {
  return (r) => {
    const u = new URL(r.request().url());
    if (u.pathname.endsWith("events.jsonl")) return r.fulfill({ status: 200, contentType: "application/x-ndjson", body: "" });
    const after = Number(u.searchParams.get("after"));
    if (after !== PROBE) afters.push(after);
    const all = getEvents();
    r.fulfill(json(200, { events: all.filter((e) => e.seq > after).slice(0, 500), last_seq: all.at(-1)?.seq ?? 0, finished: finished() }));
  };
}

const timelineRoutes = (getEvents, opts, dev = true) => ({
  [`**/api/runs/${GID}/events*`]: eventsRoute(getEvents, opts),
  "**/api/dev": (r) => r.fulfill(json(200, { trace: dev, env: false, active: dev })),
});

const steps = (page) => page.locator("ul[aria-label=Steps] > li");

test("@timeline three steps render as cards in first-seen order with model and router chips", async () => {
  const s = threeSteps();
  const { ctx, page } = await open({ routes: timelineRoutes(() => s.events), path: TL });
  await steps(page).first().waitFor();
  assert.equal(await steps(page).count(), 3);
  const titles = await steps(page).locator("button span.truncate").allTextContents();
  assert.deepEqual(titles, ["Fetch the sheet", "Parse the rows", "Write the report"]);
  const first = steps(page).nth(0);
  assert.ok(await first.getByText("Model deepseek-v4-pro").isVisible());
  assert.ok(await first.getByText("Routed by jev").isVisible());
  assert.ok(await first.getByText("decider unavailable (404)").isVisible());
  assert.ok(await first.getByText("$0.0103", { exact: true }).isVisible(), "cost is the worker call's real cost, to the digit");
  await ctx.close();
});

test("@timeline a later poll with only new events updates a card in place without reordering", async () => {
  const s = threeSteps();
  const first = 11; // the first reply ends at seq 11; step_t3 is still pending
  const all = s.events.filter((e) => e.seq <= first);
  const afters = [];
  let served = all;
  const { ctx, page } = await open({ routes: timelineRoutes(() => served, { afters, finished: () => false }), path: TL });
  await steps(page).first().waitFor();
  const titles = () => steps(page).locator("button span.truncate").allTextContents();
  const before = await titles();
  served = s.events; // adds events above `first`
  await steps(page).nth(2).getByText("Waiting").first().waitFor({ timeout: 5000 });
  assert.deepEqual(await titles(), before, "order is first-seen order and does not change");
  assert.equal(afters[0], 0);
  assert.ok(afters.slice(1).every((a) => a >= first), `later polls ask only for events after the cursor: ${afters}`);
  assert.ok(afters.at(-1) > 0);
  await ctx.close();
});

test("@timeline a step waiting on the sandbox says which attempt it is on", async () => {
  const s = threeSteps();
  const { ctx, page } = await open({ routes: timelineRoutes(() => s.events), path: TL });
  await steps(page).first().waitFor();
  const card = steps(page).nth(2);
  assert.ok(await card.getByText("Waiting for sandbox — retrying, attempt 2 of 4").isVisible());
  assert.ok(!(await card.getByText("Running", { exact: true }).count()), "waiting is its own state, not running");
  await ctx.close();
});

test("@timeline a failed command shows rc 1 and the stderr tail, with the plain-words error first", async () => {
  const s = threeSteps();
  const { ctx, page } = await open({ routes: timelineRoutes(() => s.events), path: TL });
  await steps(page).first().waitFor();
  const card = steps(page).nth(1);
  await card.getByRole("button", { name: /Parse the rows/ }).click();
  const detail = card.locator("[id^=step-][id$=-detail]");
  assert.ok(await detail.getByText("rc 1").isVisible());
  const stderr = detail.getByLabel("stderr");
  assert.ok((await stderr.textContent()).trim().endsWith("IndexError: list index out of range"));
  const text = await detail.innerText();
  assert.ok(text.indexOf("exited with an error") >= 0 && text.indexOf("exited with an error") < text.indexOf("rc 1"), "plain error precedes the command");
  assert.ok(await detail.getByText("rc=1: IndexError", { exact: false }).count(), "raw text is kept under Details");
  await ctx.close();
});

test("@timeline a call that returned no reasoning says so", async () => {
  const s = threeSteps();
  const { ctx, page } = await open({ routes: timelineRoutes(() => s.events), path: TL });
  await steps(page).first().waitFor();
  const card = steps(page).nth(1); // call_w2 has no reasoning
  await card.getByRole("button", { name: /Parse the rows/ }).click();
  assert.ok(await card.getByText("no reasoning returned").isVisible());
  const done = steps(page).nth(0);
  await done.getByRole("button", { name: /Fetch the sheet/ }).click();
  assert.equal(await done.getByText("no reasoning returned").count(), 0);
  await ctx.close();
});

test("@timeline 400 steps render few DOM rows and scrolling to the end shows step 400", async () => {
  const s = manySteps(400);
  const { ctx, page } = await open({ routes: timelineRoutes(() => s.events), path: TL });
  await steps(page).first().waitFor();
  assert.ok((await steps(page).count()) < 80, `rendered ${await steps(page).count()} rows`);
  const region = page.getByRole("region", { name: "Steps" });
  for (let i = 0; i < 40 && !(await page.getByText("Step 400", { exact: true }).count()); i++) {
    await region.evaluate((el) => { el.scrollTop = el.scrollHeight; });
    await page.waitForTimeout(60);
  }
  assert.ok(await page.getByText("Step 400", { exact: true }).isVisible());
  assert.ok((await steps(page).count()) < 80);
  await ctx.close();
});

test("@timeline dev trace off: no Timeline link on the run, and the timeline route is an honest empty state", async () => {
  const noStream = (r) => r.fulfill(json(404, { error: "dev trace off for this run" }));
  const { ctx, page } = await open({
    routes: {
      "**/api/runs*": (r) => r.fulfill(json(200, { runs: [{ run: "g_e2e01", goal: "say hi", origin: "console", device_id: null, subtasks: 1, states: {}, ts: 1 }] })),
      "**/api/runs/g_e2e01": (r) => r.fulfill(json(200, RUN)),
      "**/api/runs/g_e2e01/events*": noStream,
      [`**/api/runs/${GID}/events*`]: noStream,
      "**/api/dev": (r) => r.fulfill(json(200, { trace: false, env: false, active: false })),
    },
    path: "/#g_e2e01",
  });
  await page.getByRole("heading", { name: "say hi" }).waitFor();
  await page.waitForTimeout(300);
  assert.equal(await page.getByRole("link", { name: "Timeline" }).count(), 0);
  await page.goto(base + TL);
  await page.getByText("No timeline for this run").waitFor();
  assert.equal(await page.getByRole("link", { name: "Export JSONL" }).count(), 0);
  await ctx.close();
});

test("@timeline a run with a stream offers the Timeline link", async () => {
  const s = threeSteps();
  const { ctx, page } = await open({
    routes: {
      "**/api/runs*": (r) => r.fulfill(json(200, { runs: [{ run: GID, goal: "say hi", origin: "console", device_id: null, subtasks: 1, states: {}, ts: 1 }] })),
      [`**/api/runs/${GID}`]: (r) => r.fulfill(json(200, { ...RUN, run: GID })),
      ...timelineRoutes(() => s.events),
    },
    path: `/#${GID}`,
  });
  const link = page.getByRole("link", { name: "Timeline" });
  await link.waitFor();
  assert.equal(await link.getAttribute("href"), `#${GID}/timeline`);
  await link.click();
  await steps(page).first().waitFor();
  await ctx.close();
});

test("@timeline Export JSONL links to the events.jsonl export", async () => {
  const s = threeSteps();
  const { ctx, page } = await open({ routes: timelineRoutes(() => s.events), path: TL });
  await steps(page).first().waitFor();
  assert.equal(await page.getByRole("link", { name: "Export JSONL" }).getAttribute("href"), `/api/runs/${GID}/events.jsonl`);
  assert.ok(await page.getByText("Dev trace on").isVisible());
  await ctx.close();
});

test("@timeline the dev switch asks before turning on, and Cancel sends nothing", async () => {
  const posts = [];
  const { ctx, page } = await open({
    routes: {
      "**/api/dev": (r) => {
        if (r.request().method() === "POST") {
          posts.push({ body: r.request().postDataJSON(), csrf: r.request().headers()["x-csrf-token"] });
          return r.fulfill(json(200, { trace: true, env: false, active: true }));
        }
        return r.fulfill(json(200, { trace: false, env: false, active: false }));
      },
    },
  });
  await page.getByText("Operator", { exact: true }).click();
  const sw = page.getByRole("switch", { name: "Dev trace" });
  assert.equal(await sw.getAttribute("aria-checked"), "false");
  await sw.click();
  const warn = page.getByRole("alertdialog", { name: "Turn on Dev trace" });
  assert.ok(await warn.getByText(/program text, command output, plan JSON and model reasoning/).isVisible());
  await warn.getByRole("button", { name: "Cancel" }).click();
  assert.equal(await sw.getAttribute("aria-checked"), "false");
  assert.equal(posts.length, 0, "cancelling must not POST");
  await sw.click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Turn on" }).click();
  await page.waitForFunction(() => document.querySelector("[role=switch]")?.getAttribute("aria-checked") === "true");
  assert.equal(posts.length, 1);
  assert.deepEqual(posts[0].body, { trace: true });
  assert.ok(posts[0].csrf, "the POST carries the CSRF header");
  await ctx.close();
});

test("@timeline an environment-forced dev trace shows on and cannot be switched off", async () => {
  const { ctx, page } = await open({ routes: { "**/api/dev": (r) => r.fulfill(json(200, { trace: false, env: true, active: true })) } });
  await page.getByText("Operator", { exact: true }).click();
  const sw = page.getByRole("switch", { name: "Dev trace" });
  await page.waitForFunction(() => document.querySelector("[role=switch]")?.getAttribute("aria-checked") === "true");
  assert.ok(await sw.isDisabled());
  assert.ok(await page.getByText("Forced on by the environment").isVisible());
  await ctx.close();
});

test("@timeline @keyboard Tab reaches each card, Enter toggles it, focus is visible", async () => {
  const s = threeSteps();
  const { ctx, page } = await open({ routes: timelineRoutes(() => s.events), path: TL });
  await steps(page).first().waitFor();
  const seen = [];
  for (let i = 0; i < 20 && seen.length < 3; i++) {
    await page.keyboard.press("Tab");
    const info = await page.evaluate(() => {
      const a = document.activeElement;
      return { expanded: a?.getAttribute("aria-expanded"), visible: a?.matches(":focus-visible"), text: a?.textContent ?? "" };
    });
    if (info.expanded !== null) {
      assert.ok(info.visible, "focus is visible on the card");
      seen.push(info.text);
    }
  }
  assert.equal(seen.length, 3, "Tab reached all three cards");
  await page.keyboard.press("Shift+Tab");
  await page.keyboard.press("Shift+Tab");
  await page.keyboard.press("Tab");
  await page.keyboard.press("Enter");
  assert.equal(await page.evaluate(() => document.activeElement.getAttribute("aria-expanded")), "true");
  await page.keyboard.press("Enter");
  assert.equal(await page.evaluate(() => document.activeElement.getAttribute("aria-expanded")), "false");
  await ctx.close();
});

for (const scheme of ["light", "dark"]) {
  for (const w of [1440, 390]) {
    test(`@a11y @timeline axe: 0 violations at ${w}px, ${scheme} (cards expanded, then 400 steps)`, async () => {
      const s = threeSteps();
      let { ctx, page } = await open({ w, scheme, routes: timelineRoutes(() => s.events), path: TL });
      await steps(page).first().waitFor();
      for (const b of await steps(page).locator("button[aria-expanded]").all()) await b.click();
      await page.locator("summary", { hasText: "Reasoning" }).first().click();
      assert.deepEqual(await axeViolations(page), [], `expanded @${w} ${scheme}`);
      assert.ok(await noHScroll(page), "no horizontal scroll");
      await ctx.close();

      const many = manySteps(400);
      ({ ctx, page } = await open({ w, scheme, routes: timelineRoutes(() => many.events), path: TL }));
      await steps(page).first().waitFor();
      assert.deepEqual(await axeViolations(page), [], `virtualized @${w} ${scheme}`);
      assert.ok(await noHScroll(page), "no horizontal scroll (virtualized)");
      await ctx.close();
    });
  }
}

// Router truth: the worker model and the router decision are two separate facts on each step row.
const TRUTH_RUN = {
  ...RUN, declared: 2,
  subtasks: [
    { ...RUN.subtasks[0], subtask: "a", title: "step a", cost: 0.0102635,
      router: { engine: "jev", model: "typesafe/jev-1.13", confidence: 0.8, ms: 250,
        fallbacks: [{ engine: "decider", kind: "EngineDown", status: 404, reason: "No endpoints found for perplexity/pplx-decider-v1.1-27b" }] },
      worker: { model: "deepseek/deepseek-v4-pro", provider: "DeepInfra", tokens_in: 30, tokens_out: 12, tokens_reasoning: 0, cost: 0.0102635 } },
    { ...RUN.subtasks[0], subtask: "b", title: "step b", result: "ok",
      router: { engine: "decider", model: "perplexity/pplx-decider-v1.1-27b", confidence: 0.9, ms: 40, fallbacks: [] },
      worker: { model: "google/gemini-2.5-flash", provider: "Acme", tokens_in: 9, tokens_out: 4, tokens_reasoning: 12, cost: 0.0001 },
      thinking: "weighing the options" },
  ],
};
const truthRoutes = (run) => ({
  "**/api/runs*": (r) => r.fulfill(json(200, { runs: [{ run: "g_e2e01", goal: "say hi", origin: "console", device_id: null, subtasks: 2, states: {}, ts: 1 }] })),
  "**/api/runs/g_e2e01": (r) => r.fulfill(json(200, run)),
});

test("@flow run page: each step shows its served model and a quieter routed-by chip that names the fallback cause", async () => {
  const { ctx, page } = await open({ routes: truthRoutes(TRUTH_RUN), path: "/#g_e2e01" });
  await page.getByText("step a").waitFor();
  const a = page.locator("li", { hasText: "step a" });
  const model = a.getByText("deepseek-v4-pro", { exact: true });
  assert.equal(await model.count(), 1);
  assert.equal(await a.locator("[title^='Model deepseek/deepseek-v4-pro']").getAttribute("title"), "Model deepseek/deepseek-v4-pro via DeepInfra");
  const chip = a.locator("[title^='Routed by jev']");
  assert.equal(await chip.count(), 1);
  assert.equal(await chip.getAttribute("title"), "Routed by jev — decider unavailable (404)");
  assert.ok((await chip.textContent()).includes("routed by jev"));
  assert.ok((await chip.textContent()).includes("decider unavailable (404)"), "the cause is text for keyboard and screen-reader users too");
  assert.ok((await a.innerText()).includes("$0.0103"), "the worker cost is on the row, to the digit");
  const b = page.locator("li", { hasText: "step b" });
  assert.equal(await b.getByText("routed by decider").count(), 1);
  assert.equal(await b.locator("[title^='Routed by']").count(), 0, "no cause when the first engine answered");
  await ctx.close();
});

test("@flow run page: a finished step with zero reasoning tokens says so; one that returned reasoning and old runs do not", async () => {
  const { ctx, page } = await open({ routes: truthRoutes(TRUTH_RUN), path: "/#g_e2e01" });
  await page.getByText("step a").waitFor();
  assert.equal(await page.locator("li", { hasText: "step a" }).getByText("no reasoning returned").count(), 1);
  assert.equal(await page.locator("li", { hasText: "step b" }).getByText("no reasoning returned").count(), 0);
  await ctx.close();
  const old = await open({ routes: truthRoutes({ ...RUN, declared: 1 }), path: "/#g_e2e01" });
  const row = old.page.locator("ol li", { hasText: "say hi" });
  await row.waitFor();
  assert.equal(await row.getByText("no reasoning returned").count(), 0);
  assert.equal(await row.getByText(/routed by/).count(), 0);
  await old.ctx.close();
});

for (const scheme of ["light", "dark"]) {
  for (const w of [1440, 390]) {
    test(`@a11y run page model and routed-by chips: axe 0 violations at ${w}px, ${scheme}`, async () => {
      const { ctx, page } = await open({ w, scheme, routes: truthRoutes(TRUTH_RUN), path: "/#g_e2e01" });
      await page.getByText("routed by jev").waitFor();
      await page.evaluate(AXE);
      const r = await page.evaluate(() => axe.run(document, { runOnly: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] }));
      assert.deepEqual(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(" | ")}`), [], `${w} ${scheme}`);
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), "no horizontal scroll");
      await ctx.close();
    });
  }
}

test("@flow run page: a requeued step says it is waiting for a sandbox and which attempt", async () => {
  const now = Date.now() / 1000;
  const waiting = { ...RUN.subtasks[0], subtask: "w", title: "step waiting", state: "QUEUED", waiting_on: { kind: "sandbox_boot", attempt: 1, max: 4, since: now - 5, detail: "backend refused to boot" } };
  const plain = { ...RUN.subtasks[0], subtask: "p", title: "step plain", state: "QUEUED" };
  const run = { ...RUN, declared: 2, subtasks: [waiting, plain] };
  const { ctx, page } = await open({
    routes: {
      "**/api/runs*": (r) => r.fulfill(json(200, { runs: [{ run: "g_e2e01", goal: "say hi", origin: "console", device_id: null, subtasks: 2, states: {}, ts: 1 }] })),
      "**/api/runs/g_e2e01": (r) => r.fulfill(json(200, run)),
    },
    path: "/#g_e2e01",
  });
  await page.getByText("step waiting").waitFor();
  const row = page.locator("li", { hasText: "step waiting" });
  assert.ok(await row.getByText("Waiting for sandbox — retrying, attempt 2 of 4").isVisible());
  assert.equal(await page.locator("li", { hasText: "step plain" }).getByText("Waiting for").count(), 0);
  await ctx.close();
});

test("@flow run page: a step skipped because a dependency failed says so and shows no duration or cost", async () => {
  const step = (id, over) => ({ ...RUN.subtasks[0], subtask: id, title: `step ${id}`, ...over });
  const run = { ...RUN, declared: 2, subtasks: [
    step("a", { state: "FAILED", result: undefined, error: "sandbox exec rc=1", error_kind: "RuntimeError", started_ts: 10, finished_ts: 14 }),
    step("b", { state: "FAILED", result: undefined, error: "dep 'a' failed — cannot run", error_kind: "DependencyFailed",
      cost: 0, skipped: true, started_ts: 14, finished_ts: 14 }),
  ] };
  const { ctx, page } = await open({
    routes: {
      "**/api/runs*": (r) => r.fulfill(json(200, { runs: [{ run: "g_e2e01", goal: "say hi", origin: "console", device_id: null, subtasks: 2, states: {}, ts: 1 }] })),
      "**/api/runs/g_e2e01": (r) => r.fulfill(json(200, run)),
    },
    path: "/#g_e2e01",
  });
  await page.getByText("step b").waitFor();
  const row = page.locator("li", { hasText: "step b" });
  assert.equal(await row.getByText("Skipped — step 1 failed").count(), 1);
  assert.equal(await row.getByText(/^\d+(\.\d)?[sm]$/).count(), 0, "no duration");
  assert.equal(await row.getByText(/\$/).count(), 0, "no cost");
  assert.equal(await row.getByText("FAILED", { exact: true }).count(), 0);
  assert.equal(await page.locator("li", { hasText: "step a" }).getByText("FAILED", { exact: true }).count(), 1);
  await ctx.close();
});

// ---------------------------------------------------------------------------
// Sidebar icon rail: collapse, Ctrl/Cmd+B, remembered state, tooltips, phone width.
// ---------------------------------------------------------------------------
const RAIL_SHOTS = path.join(os.homedir(), "oc-shots");
const rail = (page) => page.locator("aside#app-sidebar");

test("@rail Ctrl+B collapses to an icon rail with named, tooltipped icons; the choice survives a reload", async () => {
  const { ctx, page } = await open();
  assert.equal(await rail(page).getAttribute("data-collapsed"), "false", "expanded by default on desktop");
  await page.keyboard.press("Control+b");
  assert.equal(await rail(page).getAttribute("data-collapsed"), "true");
  assert.ok((await rail(page).boundingBox()).width <= 60, "rail is narrow");
  assert.equal(await page.evaluate(() => localStorage.getItem("oc.sidebarCollapsed")), "1");
  for (const name of ["New chat", "Agent jobs", "Expand sidebar"]) {
    const b = rail(page).getByRole("button", { name });
    assert.equal(await b.getAttribute("title") ?? name, name === "Expand sidebar" ? "Expand sidebar (Ctrl+B)" : name);
  }
  await rail(page).getByRole("button", { name: "Agent jobs" }).hover();
  assert.ok(await rail(page).getByText("Agent jobs", { exact: true }).isVisible(), "tooltip shows on hover");
  assert.equal(await page.getByText("Recents").count(), 0, "chat list is not rendered in the rail");
  await page.reload({ waitUntil: "networkidle" });
  assert.equal(await rail(page).getAttribute("data-collapsed"), "true", "remembered after reload");
  assert.deepEqual(await axeViolations(page), [], "rail axe");
  fs.mkdirSync(RAIL_SHOTS, { recursive: true });
  await page.screenshot({ path: path.join(RAIL_SHOTS, "rail-collapsed-light.png") });
  await rail(page).getByRole("button", { name: "Expand sidebar" }).click();
  assert.equal(await rail(page).getAttribute("data-collapsed"), "false");
  assert.equal(await page.evaluate(() => localStorage.getItem("oc.sidebarCollapsed")), "0");
  await page.keyboard.press("Meta+b");
  assert.equal(await rail(page).getAttribute("data-collapsed"), "true", "Cmd+B works too");
  await ctx.close();
});

test("@rail dark rail screenshot has 0 axe violations", async () => {
  const { ctx, page } = await open({ scheme: "dark" });
  await page.keyboard.press("Control+b");
  assert.deepEqual(await axeViolations(page), []);
  await page.screenshot({ path: path.join(RAIL_SHOTS, "rail-collapsed-dark.png") });
  await ctx.close();
});

test("@rail phone width starts as a rail, opens as an overlay, closes on choosing, never scrolls sideways", async () => {
  const { ctx, page } = await open({ w: 390, h: 800 });
  assert.equal(await rail(page).getAttribute("data-collapsed"), "true", "phones start collapsed");
  assert.ok(await noHScroll(page));
  assert.ok(await page.getByRole("textbox", { name: "Message" }).isVisible());
  assert.ok((await page.getByRole("textbox", { name: "Message" }).boundingBox()).width > 250, "the composer keeps room");
  await rail(page).getByRole("button", { name: "Expand sidebar" }).click();
  assert.equal(await rail(page).getAttribute("data-collapsed"), "false");
  assert.ok(await noHScroll(page));
  assert.deepEqual(await axeViolations(page), [], "open overlay axe");
  await page.screenshot({ path: path.join(RAIL_SHOTS, "rail-mobile-open.png") });
  await rail(page).getByRole("button", { name: "New chat" }).click();
  assert.equal(await rail(page).getAttribute("data-collapsed"), "true", "choosing puts the overlay away");
  await page.screenshot({ path: path.join(RAIL_SHOTS, "rail-mobile.png") });
  await ctx.close();
});

test("@flow settings: Connect OpenRouter starts PKCE, the callback exchanges once, Disconnect resets", async () => {
  let connected = false;
  const posts = [];
  const { ctx, page } = await open({
    path: "/settings",
    routes: {
      "**/api/openrouter**": async (r) => {
        const u = new URL(r.request().url());
        if (r.request().method() === "GET") return r.fulfill(json(200, { connected, source: connected ? "omaseal" : null }));
        posts.push([u.pathname, r.request().postData()]);
        if (u.pathname.endsWith("/connect")) return r.fulfill(json(200, { url: base + "/__or_signin?x=1" }));
        connected = u.pathname.endsWith("/complete");
        return r.fulfill(json(200, { connected, source: connected ? "omaseal" : null }));
      },
      "**/__or_signin*": (r) => r.fulfill({ status: 200, contentType: "text/html", body: "<title>OpenRouter sign-in (mock)</title>" }),
    },
  });
  await page.getByText("Not connected.").waitFor();
  await page.getByRole("button", { name: "Connect OpenRouter" }).click();
  await page.waitForURL("**/__or_signin*");
  assert.deepEqual(JSON.parse(posts[0][1]), { origin: base });
  // OpenRouter sends the user back with ?code=...; the console exchanges it once and cleans the URL.
  await page.goto(base + "/settings?code=abc123&oc_state=st1", { waitUntil: "networkidle" });
  await page.getByText("Connected. Your key is stored in the host keyring").waitFor();
  const done = posts.filter(([p]) => p.endsWith("/complete"));
  assert.equal(done.length, 1);
  assert.deepEqual(JSON.parse(done[0][1]), { code: "abc123", state: "st1" });
  assert.equal(new URL(page.url()).search, "");
  await page.getByRole("button", { name: "Disconnect" }).click();
  await page.getByText("Not connected.").waitFor();
  assert.deepEqual(await page.evaluate(() => [localStorage.length, sessionStorage.length]), [0, 0], "no key or code in browser storage");
  await ctx.close();
});

// ---------------------------------------------------------------------------
// Effects: "Reduce effects" switch, motion gates, light and dark shots.
// ---------------------------------------------------------------------------
const SHOTS = path.join(os.homedir(), "oc-shots");

const liveStream = () => {
  const s = stream();
  s.add("run", { status: "running", title: "Summarise the sales sheet", cap_usd: 0.5 });
  s.add("step", { id: "step_t1", title: "Fetch the sheet", status: "done" });
  s.add("step", { id: "step_t2", title: "Parse the rows", status: "running" });
  s.add("step", { id: "step_t3", title: "Write the report", status: "pending" });
  s.add("cost", { spent_usd: 0.004, cap_usd: 0.5 });
  return s;
};
const cssAnimations = (page) =>
  page.evaluate(() => document.getAnimations().filter((a) => a instanceof CSSAnimation && a.playState === "running").map((a) => a.animationName));

test("@effects the Reduce effects switch sets data-effects on <html> and survives a reload", async () => {
  const { ctx, page } = await open({ path: "/settings", motion: "no-preference" });
  const html = page.locator("html");
  const box = page.getByRole("checkbox", { name: /Reduce effects/ });
  assert.equal(await html.getAttribute("data-effects"), null, "effects are on by default when the OS does not ask for less motion");
  assert.equal(await box.isChecked(), false);
  await box.check();
  assert.equal(await html.getAttribute("data-effects"), "off");
  await page.reload({ waitUntil: "networkidle" });
  assert.equal(await html.getAttribute("data-effects"), "off", "remembered after reload");
  assert.equal(await page.getByRole("checkbox", { name: /Reduce effects/ }).isChecked(), true);
  assert.equal(await page.evaluate(() => localStorage.getItem("oc-effects")), "off");
  await page.getByRole("checkbox", { name: /Reduce effects/ }).uncheck();
  await page.reload({ waitUntil: "networkidle" });
  assert.equal(await html.getAttribute("data-effects"), null);
  await ctx.close();
});

test("@effects with no stored choice the OS reduced-motion preference turns effects off", async () => {
  const { ctx, page } = await open({ path: "/settings", motion: "reduce" });
  assert.equal(await page.locator("html").getAttribute("data-effects"), "off");
  assert.equal(await page.getByRole("checkbox", { name: /Reduce effects/ }).isChecked(), true);
  await ctx.close();
});

test("@effects reduced motion and the switch each leave no running animations; effects on animate", async () => {
  const s = liveStream();
  const routes = timelineRoutes(() => s.events, { finished: () => false });
  const run = async (motion, stored) => {
    const { ctx, page } = await open({ routes, path: TL, motion, setup: stored ? (p) => p.addInitScript((v) => localStorage.setItem("oc-effects", v), stored) : undefined });
    await steps(page).nth(1).waitFor();
    const names = await cssAnimations(page);
    await ctx.close();
    return names;
  };
  assert.deepEqual(await run("reduce"), [], "prefers-reduced-motion: no running animations");
  assert.deepEqual(await run("no-preference", "off"), [], "switch off: no running animations");
  const on = await run("no-preference");
  for (const n of ["fx-sheen", "fx-pulse", "fx-edge-run"]) assert.ok(on.includes(n), `effects on: ${n} runs (${on})`);
});

for (const scheme of ["light", "dark"]) {
  test(`@effects ${scheme}: weave and running timeline have 0 axe violations; screenshot saved`, async () => {
    const s = liveStream();
    const routes = timelineRoutes(() => s.events, { finished: () => false });
    const { ctx, page } = await open({ routes, path: TL, scheme, motion: "no-preference" });
    await steps(page).nth(1).waitFor();
    assert.deepEqual(await axeViolations(page), [], `timeline ${scheme}`);
    fs.mkdirSync(SHOTS, { recursive: true });
    await page.screenshot({ path: path.join(SHOTS, `effects-${scheme}.png`) });
    await ctx.close();
  });
}

// Ambient shader (components/Ambient.tsx): draws only when motion is allowed, replaces the weave while it draws,
// and is never there under reduced motion or Reduce effects. Headless Chromium has WebGL through SwiftShader.
const weaveShown = (page) => page.evaluate(() => getComputedStyle(document.body, "::before").display !== "none");

test("@effects ambient: motion allowed draws the shader in place of the weave; reduced motion and the switch keep the weave", async () => {
  {
    const { ctx, page } = await open({ motion: "no-preference" });
    await page.locator("html[data-ambient=on]").waitFor({ state: "attached" });
    assert.equal(await page.getByTestId("ambient").count(), 1);
    assert.equal(await page.getByTestId("ambient").getAttribute("aria-hidden"), "true");
    assert.equal(await weaveShown(page), false, "the shader replaces the weave");
    // the shader sits behind everything and never takes a click
    assert.equal(await page.getByTestId("ambient").evaluate((c) => getComputedStyle(c).pointerEvents), "none");
    await page.getByRole("textbox", { name: "Message" }).click();
    await ctx.close();
  }
  for (const [motion, stored] of [["reduce", undefined], ["no-preference", "off"]]) {
    const { ctx, page } = await open({ motion, setup: stored ? (p) => p.addInitScript((v) => localStorage.setItem("oc-effects", v), stored) : undefined });
    await page.getByRole("textbox", { name: "Message" }).waitFor();
    assert.equal(await page.getByTestId("ambient").count(), 0, `${motion}/${stored}: no canvas`);
    assert.equal(await page.locator("html").getAttribute("data-ambient"), null);
    assert.equal(await weaveShown(page), true, `${motion}/${stored}: weave stays`);
    await ctx.close();
  }
});

test("@effects ambient: turning Reduce effects on stops the shader and brings the weave back", async () => {
  const { ctx, page } = await open({ path: "/settings", motion: "no-preference" });
  await page.locator("html[data-ambient=on]").waitFor({ state: "attached" });
  await page.getByRole("checkbox", { name: /Reduce effects/ }).check();
  await page.waitForFunction(() => !document.documentElement.hasAttribute("data-ambient"));
  assert.equal(await page.getByTestId("ambient").count(), 0);
  assert.equal(await weaveShown(page), true);
  await ctx.close();
});

test("@effects ambient: nothing moves on the approvals page", async () => {
  const { ctx, page } = await open({ path: "/approvals", motion: "no-preference" });
  await page.locator("main").first().waitFor();
  assert.equal(await page.getByTestId("ambient").count(), 0);
  assert.equal(await page.locator("html").getAttribute("data-ambient"), null);
  await ctx.close();
});

for (const scheme of ["light", "dark"]) {
  test(`@effects ambient ${scheme}: 0 axe violations with the shader on; screenshot saved`, async () => {
    const { ctx, page } = await open({ scheme, motion: "no-preference" });
    await page.locator("html[data-ambient=on]").waitFor({ state: "attached" });
    await page.waitForTimeout(400); // a few frames so the screenshot shows the field
    assert.deepEqual(await axeViolations(page), [], `home ${scheme}`);
    fs.mkdirSync(SHOTS, { recursive: true });
    await page.screenshot({ path: path.join(SHOTS, `ambient-${scheme}.png`) });
    await ctx.close();
  });
}

// Live view for everyone: a run with a stream opens on the live view; dev mode off hides raw model detail.
for (const scheme of ["light", "dark"]) {
  test(`@live the run page opens on the live view with sandbox activity, no dev mode needed (${scheme})`, async () => {
    const s = threeSteps();
    const { ctx, page } = await open({
      scheme,
      routes: {
        "**/api/runs*": (r) => r.fulfill(json(200, { runs: [{ run: GID, goal: "say hi", origin: "console", device_id: null, subtasks: 1, states: {}, ts: 1 }] })),
        [`**/api/runs/${GID}`]: (r) => r.fulfill(json(200, { ...RUN, run: GID })),
        ...timelineRoutes(() => s.events, undefined, false),
      },
      path: `/#${GID}`,
    });
    await steps(page).first().waitFor();
    assert.equal(await steps(page).count(), 3);
    await page.getByTestId("activity").waitFor();
    assert.ok(await page.getByTestId("activity").getByText("python3 parse.py").isVisible(), "latest command is shown");
    assert.equal(await page.getByRole("tab", { name: "Live" }).getAttribute("aria-selected"), "true");
    // raw reasoning stays behind dev mode
    await steps(page).first().getByRole("button").click();
    assert.equal(await page.getByText("Download the CSV then count rows.").count(), 0);
    assert.deepEqual(await axeViolations(page), []);
    await page.screenshot({ path: process.env.SHOTS ? `${process.env.SHOTS}/live-view-${scheme}.png` : "/tmp/live-view.png" });
    await page.getByRole("tab", { name: "Steps" }).click();
    await page.locator("ol > li").getByText("say hi").waitFor();
    assert.equal(await steps(page).count(), 0);
    await ctx.close();
  });
}

// Run files: listed under the run, previewed as text, downloadable one by one or as a zip.
for (const scheme of ["light", "dark"]) {
  test(`@files a run lists its files with preview and download (${scheme})`, async () => {
    const { ctx, page } = await open({
      scheme,
      routes: {
        "**/api/runs*": (r) => r.fulfill(json(200, { runs: [{ run: "g_e2e01", goal: "say hi", origin: "console", device_id: null, subtasks: 1, states: {}, ts: 1 }] })),
        "**/api/runs/g_e2e01": (r) => r.fulfill(json(200, RUN)),
        "**/api/runs/g_e2e01/artifacts": (r) => r.fulfill(json(200, { artifacts: [
          { name: "report.md", step: "s1", size: 2048, mime: "text/markdown" },
          { name: "step-s1.md", step: "s1", size: 2, mime: "text/markdown" },
          { name: "big.csv", step: "s1", refused: "too large (1200 KB; the limit is 1000 KB)" },
        ] })),
        "**/api/runs/g_e2e01/artifacts/report.md": (r) => r.fulfill({ status: 200, contentType: "text/markdown", body: "# Report\nall good" }),
      },
      path: "/#g_e2e01",
    });
    await page.getByTestId("artifacts").waitFor();
    assert.equal(await page.getByRole("link", { name: "Download report.md" }).getAttribute("href"), "/api/runs/g_e2e01/artifacts/report.md?download=1");
    assert.equal(await page.getByRole("link", { name: "Download all (.zip)" }).getAttribute("href"), "/api/runs/g_e2e01/artifacts.zip");
    await page.getByText("not saved: too large").waitFor();
    await page.getByText("report.md").first().click();
    await page.getByLabel("Preview of report.md").getByText("all good").waitFor();
    assert.deepEqual(await axeViolations(page), []);
    await page.screenshot({ path: process.env.SHOTS ? `${process.env.SHOTS}/files-${scheme}.png` : "/tmp/files.png" });
    await ctx.close();
  });
}

// Plan review: a held plan is shown before anything runs; edits and the decision go to the plan endpoint.
const PLANNED = {
  run: "g_e2e01", goal: "compare two things", origin: "console", device_id: null, declared: 2, worker_failures: [], subtasks: [],
  plan: { state: "planned", estimate: 0.08, budget: 0.1, model: "deepseek/deepseek-v4-pro", steps: [
    { id: "a", title: "gather facts", prompt: "collect the facts", deps: [], tier: "mid", est_cost: 0.04 },
    { id: "b", title: "write summary", prompt: "summarise them", deps: ["a"], tier: "cheap", est_cost: 0.04 },
  ] },
};

for (const scheme of ["light", "dark"]) {
  test(`@plan a held plan shows before spend, edits post to the plan endpoint (${scheme})`, async () => {
    const posts = [];
    const { ctx, page } = await open({
      scheme,
      routes: {
        "**/api/runs*": (r) => r.fulfill(json(200, { runs: [{ run: "g_e2e01", goal: "compare two things", origin: "console", device_id: null, subtasks: 2, states: {}, ts: 1, plan_state: "planned" }] })),
        "**/api/runs/g_e2e01": (r) => r.fulfill(json(200, PLANNED)),
        "**/api/runs/g_e2e01/plan": (r) => { posts.push(r.request().postData()); return r.fulfill(json(200, { state: "approved" })); },
      },
      path: "/#g_e2e01",
    });
    await page.getByTestId("plan-card").waitFor();
    await page.getByText("Plan · nothing has run yet").waitFor();
    await page.getByText(/estimate \$0\.08/).waitFor();
    assert.deepEqual(await axeViolations(page), []);
    await page.screenshot({ path: process.env.SHOTS ? `${process.env.SHOTS}/plan-review-${scheme}.png` : "/tmp/plan-review.png" });
    await page.getByLabel("Step 2 title").fill("write the final summary");
    await page.getByRole("button", { name: "Remove step" }).first().click();
    await page.getByRole("button", { name: "Approve with edits" }).click();
    await page.getByText("Decision saved").waitFor();
    assert.equal(posts.length, 1);
    const sent = JSON.parse(posts[0]);
    assert.equal(sent.action, "approve");
    assert.deepEqual(sent.edits, [{ id: "a", remove: true }, { id: "b", title: "write the final summary", prompt: "summarise them", tier: "cheap" }]);
    await ctx.close();
  });
}

test("@plan cancel posts a cancel with no edits", async () => {
  const posts = [];
  const { ctx, page } = await open({
    routes: {
      "**/api/runs*": (r) => r.fulfill(json(200, { runs: [] })),
      "**/api/runs/g_e2e01": (r) => r.fulfill(json(200, PLANNED)),
      "**/api/runs/g_e2e01/plan": (r) => { posts.push(JSON.parse(r.request().postData())); return r.fulfill(json(200, { state: "cancelled" })); },
    },
    path: "/#g_e2e01",
  });
  await page.getByRole("button", { name: "Cancel run" }).click();
  await page.getByText("Decision saved").waitFor();
  assert.deepEqual(posts, [{ action: "cancel", edits: [] }]);
  await ctx.close();
});
