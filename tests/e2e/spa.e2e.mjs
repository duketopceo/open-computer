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
  server = spawn(PY[0], [...PY.slice(1), "-I", "tests/opsview/serve_fixture.py", "8871", dir], { cwd: REPO });
  server.stderr.on("data", (d) => process.stderr.write(d));
  await new Promise((res, rej) => {
    server.stdout.on("data", (d) => String(d).includes("ready") && res());
    server.on("exit", (c) => rej(new Error("fixture exited " + c)));
    setTimeout(() => rej(new Error("fixture timeout")), 30000);
  });
  base = "http://127.0.0.1:8871";
});
after(async () => { await browser?.close(); server?.kill(); });

const json = (status, body) => ({ status, contentType: "application/json", body: JSON.stringify(body) });

async function open({ w = 1440, h = 900, routes = {} } = {}) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, reducedMotion: "reduce" });
  await ctx.addCookies([{ name: "oc_sess", value: "test", url: base }]);
  const page = await ctx.newPage();
  // Empty runs list by default so the composer (not a run) is the landing view.
  await page.route("**/api/runs", (r) => r.fulfill(json(200, { runs: [] })));
  for (const [glob, handler] of Object.entries(routes)) await page.route(glob, handler);
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
