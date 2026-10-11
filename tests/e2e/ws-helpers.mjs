// Shared harness for the workspace browser gates: fixture servers, page opener, axe, small API helpers.
import { before, after } from "node:test";
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
export const AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"];

const servers = [];
async function serve(port) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "oc-ws-e2e-"));
  const p = spawn(PY[0], [...PY.slice(1), "-I", "tests/opsview/serve_fixture.py", String(port), dir, "--replay"], { cwd: REPO });
  servers.push(p);
  await new Promise((res, rej) => {
    p.stdout.on("data", (d) => String(d).includes("ready") && res());
    p.on("exit", (c) => rej(new Error("fixture exited " + c)));
    setTimeout(() => rej(new Error("fixture timeout")), 30000);
  });
  return `http://127.0.0.1:${port}`;
}

// Call once per test file: launches a browser and one replay fixture server per port. Servers are separate so a test
// that decides approvals or fills a stream can't disturb another test's goals.
export const S = {};
let browser;
export function harness(ports) {
  before(async () => {
    browser = await chromium.launch();
    const bases = await Promise.all(ports.map(serve));
    ports.forEach((_, i) => { S[["RO", "RW", "LIVE"][i]] = bases[i]; });
  });
  after(async () => { await browser?.close(); servers.forEach((s) => s.kill()); });
}

export async function open(base, goal, { theme = "dark", w = 1440, h = 900, q = "speed=0&restart=1", route = true } = {}) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: theme, reducedMotion: "reduce" });
  await ctx.addCookies([{ name: "oc_sess", value: "test", url: base }]);
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  await page.addInitScript((t) => { try { if (!localStorage.getItem("oc-theme")) localStorage.setItem("oc-theme", t); } catch {} }, theme);
  await page.goto(`${base}/workspace${goal ? "/" + goal : ""}${q ? "?" + q : ""}`);
  page.errors = errors;
  return { ctx, page };
}
export const complete = (page) => page.waitForFunction(() => document.getElementById("ws").dataset.stream === "complete", null, { timeout: 20000 });
export const held = (page, sel = "#dock .acard") => page.waitForSelector(sel, { timeout: 20000 });
export async function axe(page, tags = AXE_TAGS) {
  await page.evaluate(AXE);
  const r = await page.evaluate((t) => axe.run(document, { runOnly: t }), tags);
  return r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(" | ")}`);
}
export const frameOf = async (page, needle) => { for (let i = 0; i < 100; i++) { const f = page.frames().find((x) => x.url().includes(needle)); if (f) return f; await page.waitForTimeout(50); } throw new Error("no frame " + needle); };
export const csrfOf = (page) => page.evaluate(() => document.querySelector("meta[name=csrf-token]").content);
export const api = async (page, url, body) => page.evaluate(async ([u, b, t]) => {
  const r = await fetch(u, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-CSRF-Token": t }, body: JSON.stringify(b) });
  return { status: r.status, body: await r.json() };
}, [url, body, await csrfOf(page)]);
export const approvalsOf = (page, goal) => page.evaluate(async (g) => (await (await fetch(`/goals/${g}/approvals`, { credentials: "same-origin" })).json()).approvals, goal);
export const tabsOpen = async (page, w) => { if (w < 768) { const o = await page.getAttribute("#ws", "data-sheet"); if (o !== "open") await page.click('[data-strip="sources"]'); } };

