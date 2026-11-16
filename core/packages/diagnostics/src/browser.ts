// Web wizard: renders the chain from wizard.ts. Bundled into the cloud core's setup page by scripts/build-wizard.mjs.
import { buildReport, runWizard, stepsFor, type Backend, type Row } from "./wizard.ts";

declare const __APP_VERSION__: string;

const $ = <T extends HTMLElement>(id: string): T => document.getElementById(id) as T;
const TOKEN_KEY = "oc.token";

function readToken(): string {
  try {
    return sessionStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls?: string, text?: string): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

const ICON: Record<string, string> = { pass: "✓", fail: "✕", skip: "–", pending: "·" };

function render(rows: Row[], list: HTMLElement): void {
  list.textContent = "";
  let group = "";
  for (const r of rows) {
    if (r.group !== group) {
      group = r.group;
      list.append(el("h2", "group", group));
    }
    const li = el("li", `row ${r.status}`);
    const mark = el("span", "mark");
    if (r.status === "running") mark.append(el("span", "spinner"));
    else mark.textContent = ICON[r.status] ?? "";
    mark.setAttribute("aria-label", r.status);
    const body = el("div", "body");
    const head = el("div", "head");
    head.append(el("span", "title", r.title));
    if (r.ms !== undefined) head.append(el("span", "time", r.ms >= 1000 ? `${(r.ms / 1000).toFixed(1)} s` : `${Math.round(r.ms)} ms`));
    body.append(head);
    if (r.detail) body.append(el("p", "detail", r.detail));
    if (r.metrics?.length) {
      const dl = el("dl", "metrics");
      for (const m of r.metrics) {
        dl.append(el("dt", undefined, m.label), el("dd", undefined, m.value));
      }
      body.append(dl);
    }
    if (r.fix) {
      const fix = el("p", "fix");
      fix.append(el("strong", undefined, "Fix: "), document.createTextNode(r.fix.text));
      body.append(fix);
    }
    li.append(mark, body);
    list.append(li);
  }
}

let running = false;
let lastRows: Row[] = [];

async function start(): Promise<void> {
  if (running) return;
  const token = $<HTMLInputElement>("token").value.trim();
  if (!token) {
    $("status").textContent = "Enter the access token first.";
    return;
  }
  try {
    sessionStorage.setItem(TOKEN_KEY, token);
  } catch {
    /* the token then lives only in this field */
  }
  const backend = $<HTMLSelectElement>("backend").value as Backend;
  const base = backend === "cloudflare" ? location.origin : $<HTMLInputElement>("url").value.trim();
  running = true;
  $<HTMLButtonElement>("run").disabled = true;
  $("status").textContent = "Running…";
  $("summary").hidden = true;
  const list = $("rows");
  const rows = await runWizard(
    stepsFor(backend),
    { baseUrl: base, token, appVersion: __APP_VERSION__, backend, fetch: (...a) => fetch(...a), now: () => performance.now(), pings: 20, cache: {} },
    (r) => render(r, list),
  );
  lastRows = rows;
  const report = buildReport(rows, { backend, appVersion: __APP_VERSION__ });
  const sum = $("summary");
  sum.hidden = false;
  sum.textContent = "";
  sum.append(
    el("h2", undefined, report.failed === 0 && report.skipped === 0 ? "All checks passed" : `${report.failed} failed, ${report.skipped} skipped`),
    el("p", undefined, `${report.passed} passed in ${(report.total_ms / 1000).toFixed(1)} s. Test spend $${report.test_cost_usd.toFixed(4)}.`),
  );
  $("status").textContent = "";
  $<HTMLButtonElement>("run").disabled = false;
  $<HTMLButtonElement>("run").textContent = "Run tests again";
  $<HTMLButtonElement>("export").hidden = false;
  running = false;
}

function exportJson(): void {
  const backend = $<HTMLSelectElement>("backend").value as Backend;
  const text = JSON.stringify(buildReport(lastRows, { backend, appVersion: __APP_VERSION__ }), null, 2);
  const a = el("a");
  a.href = URL.createObjectURL(new Blob([text], { type: "application/json" }));
  a.download = "open-computer-test-results.json";
  a.click();
  URL.revokeObjectURL(a.href);
}

function init(): void {
  $<HTMLInputElement>("token").value = readToken();
  const backend = $<HTMLSelectElement>("backend");
  const sync = (): void => {
    $("url-row").hidden = backend.value === "cloudflare";
  };
  backend.addEventListener("change", sync);
  sync();
  $("run").addEventListener("click", () => void start());
  $("export").addEventListener("click", exportJson);
  render(
    stepsFor("cloudflare").map((d) => ({ id: d.id, group: d.group, title: d.title, status: "pending" as const })),
    $("rows"),
  );
  if (new URLSearchParams(location.search).get("autorun") === "1" && readToken()) void start();
}

init();
