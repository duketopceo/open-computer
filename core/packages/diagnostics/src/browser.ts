// Web wizard: renders the chain from wizard.ts. Bundled into the cloud core's setup page by scripts/build-wizard.mjs.
import { Chat, readSse, writeDiff, type Item } from "./chat.ts";
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
  // The progress bar fills as rows settle.
  const done = rows.filter((r) => r.status === "pass" || r.status === "fail" || r.status === "skip").length;
  const pct = rows.length ? Math.round((done / rows.length) * 100) : 0;
  const bar = document.getElementById("bar");
  if (bar) {
    bar.style.width = `${pct}%`;
    bar.parentElement?.setAttribute("aria-valuenow", String(pct));
  }
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

// ---- chat and settings ----

const chat = new Chat();
let workingTimer: ReturnType<typeof setInterval> | undefined;
let startedAt = 0;
const SESSION = `web-${Math.random().toString(36).slice(2, 10)}`;

function authHeaders(): Record<string, string> {
  return { authorization: `Bearer ${$<HTMLInputElement>("token").value.trim()}`, "content-type": "application/json" };
}
const base = (): string => ($<HTMLSelectElement>("backend").value === "cloudflare" ? location.origin : $<HTMLInputElement>("url").value.trim().replace(/\/+$/, ""));

function itemEl(it: Item, i: number): HTMLElement {
  const li = el("li", "msg");
  const who = (t: string) => li.append(el("div", "who", t));
  switch (it.kind) {
    case "user":
      who("You");
      li.append(el("pre", undefined, it.text));
      break;
    case "thinking": {
      li.className = "msg thought";
      const d = el("details");
      d.open = !it.done;
      d.append(el("summary", undefined, it.done ? "Thought" : "Thinking"), el("pre", undefined, it.text));
      li.append(d);
      break;
    }
    case "assistant":
      who(it.done ? "Open Computer" : "Open Computer is writing");
      li.append(el("pre", undefined, it.text));
      break;
    case "tool": {
      li.className = "msg tool";
      const head = el("div");
      head.append(el("span", "name", it.name), document.createTextNode(`  ${it.status}${it.risk ? `  risk: ${it.risk}` : ""}${it.ms !== undefined ? `  ${Math.round(it.ms)} ms` : ""}`));
      li.append(head);
      const d = el("details");
      d.open = it.status === "running";
      if (it.args) d.append(el("pre", undefined, writeDiff(it.args) ?? it.args.slice(0, 600)));
      if (it.output !== undefined) d.append(el("pre", undefined, it.output.slice(0, 1200)));
      if (d.childNodes.length > 0) {
        d.prepend(el("summary", undefined, "Details"));
        li.append(d);
      }
      break;
    }
    case "approval": {
      li.className = "msg approval";
      const word = it.state === "requested" ? "Needs your decision" : it.state;
      li.append(el("div", "who", `Approval: ${word}`), el("pre", undefined, it.summary));
      if (it.state === "requested") {
        const acts = el("div", "acts");
        const yes = el("button", undefined, "Approve");
        const no = el("button", "secondary", "Deny");
        yes.type = no.type = "button";
        yes.onclick = () => void decide(it.id, it.hash, true);
        no.onclick = () => void decide(it.id, it.hash, false);
        acts.append(yes, no);
        li.append(acts);
      }
      break;
    }
    case "step":
      li.className = "note";
      li.textContent = `step ${it.step + 1}: ${it.prompt} in, ${it.completion} out${it.reasoning ? ` (${it.reasoning} reasoning)` : ""}${it.cost !== null ? `, $${it.cost.toFixed(5)}` : ""}`;
      break;
    case "note":
      li.className = "note";
      li.textContent = it.text;
      break;
  }
  li.dataset.i = String(i);
  return li;
}

function renderChat(): void {
  const list = $("chat-items");
  list.textContent = "";
  chat.items.forEach((it, i) => list.append(itemEl(it, i)));
  const working = $("chat-working");
  working.hidden = !(chat.busy && !chat.hasReasoning);
  working.textContent = `Working \u00b7 ${Math.floor((Date.now() - startedAt) / 1000)}s`;
  $<HTMLButtonElement>("send").disabled = chat.busy;
  $("stop").hidden = !chat.busy;
  const c = chat.cost;
  $("chat-footer").textContent = `z-ai/glm-5.3  |  ${c ? `task $${c.task.toFixed(4)} of $${c.taskCap.toFixed(2)}  |  today $${c.day.toFixed(4)} of ${c.dayCap === null ? "no cap" : `$${c.dayCap.toFixed(2)}`}` : "no spend yet"}  |  ${chat.status || "idle"}`;
  list.lastElementChild?.scrollIntoView({ block: "nearest" });
}

async function decide(id: string, hash: string, approved: boolean): Promise<void> {
  const r = await fetch(`${base()}/v1/sessions/${SESSION}/approvals/${id}`, { method: "POST", headers: authHeaders(), body: JSON.stringify({ hash, approved }) });
  if (!r.ok) chat.items.push({ kind: "note", text: "The decision was not recorded." });
  renderChat();
}

async function sendGoal(): Promise<void> {
  const input = $<HTMLInputElement>("goal");
  const goal = input.value.trim();
  if (!goal || chat.busy) return;
  input.value = "";
  chat.pushUser(goal);
  startedAt = Date.now();
  renderChat();
  clearInterval(workingTimer);
  workingTimer = setInterval(renderChat, 1000);
  try {
    const res = await fetch(`${base()}/v1/tasks`, { method: "POST", headers: authHeaders(), body: JSON.stringify({ goal, session_id: SESSION }) });
    const body = (await res.json().catch(() => ({}))) as { error?: { message?: string } };
    if (res.status !== 202) throw new Error(body.error?.message ?? `The core answered ${res.status}.`);
    const events = await fetch(`${base()}/v1/sessions/${SESSION}/events`, { headers: { authorization: authHeaders().authorization as string } });
    await readSse(events, (e) => {
      chat.apply(e);
      renderChat();
    });
  } catch (e) {
    chat.items.push({ kind: "note", text: e instanceof Error ? e.message : "The task could not be started." });
    chat.status = "failed";
  } finally {
    clearInterval(workingTimer);
    renderChat();
  }
}

async function stopTask(): Promise<void> {
  if (chat.taskId) await fetch(`${base()}/v1/sessions/${SESSION}/tasks/${chat.taskId}/cancel`, { method: "POST", headers: authHeaders() });
}

async function renderSettings(): Promise<void> {
  const body = $("settings-body");
  body.textContent = "Loading\u2026";
  const r = await fetch(`${base()}/v1/settings`, { headers: authHeaders() }).catch(() => null);
  const s = r && r.ok ? ((await r.json()) as Record<string, any>) : null;
  body.textContent = "";
  const sec = (title: string, ...lines: string[]): HTMLElement => {
    const d = el("div", "set");
    d.append(el("h3", undefined, title));
    for (const l of lines) d.append(el("p", "detail", l));
    body.append(d);
    return d;
  };
  sec("Models and keys", "Default z-ai/glm-5.3, worker z-ai/glm-5.3-flash.", "The OpenRouter key lives in the core's secret store and is never shown. Custom endpoint and Codex: not available in this release.");
  sec("Routing", "Direct to OpenRouter. Prices come from OpenRouter and refresh daily.");
  sec("Sandbox", "One fresh sandbox per task. No key inside; outbound internet closed.");
  const spend = sec("Spending", s ? `Daily cap in effect: $${Number(s.effective_daily_cap_usd).toFixed(2)}${s.policy_max_daily_cap_usd ? ` (held by your IT policy at $${s.policy_max_daily_cap_usd})` : ""}.` : "Could not read settings. Check the token.");
  const row = el("div", "row2");
  for (const [label, v] of [["$1", 1], ["$5 (default)", null], ["$20", 20], ["$50", 50]] as const) {
    const b = el("button", "secondary", label);
    b.type = "button";
    b.onclick = async () => {
      await fetch(`${base()}/v1/settings`, { method: "PUT", headers: authHeaders(), body: JSON.stringify({ daily_cap_usd: v }) });
      void renderSettings();
    };
    row.append(b);
  }
  spend.append(row);
  sec("Permissions and approvals", "Reading runs freely. Writing files, running commands and using the network ask first; each approval is tied to the exact action, works once, and expires.");
  sec("Memory", "On: summaries of finished tasks are kept and recalled with a source.");
  sec("Data and privacy", "Prompts and answers are not written to the audit log. Traces, when enabled, carry names, timings, tokens and cost only.");
  sec("Appearance", "Black and white; dark by default, light follows your system.");
  sec("Shortcuts", "Enter sends. Tab moves between controls.");
  sec("Integrations, MCP and plugins", "Not available in this release.");
  sec("Voice", "Not available in this release.");
  sec("Developer", "Same tests from the terminal: `oc doctor`. API: POST /v1/tasks, GET /v1/sessions/:id/events.");
  sec("Policy and diagnostics", "An IT policy file can lock the daily cap, limit models and tools, and set an OpenTelemetry endpoint. Run the Tests tab for the full 19-row chain.");
}

function showTab(name: "tests" | "chat" | "settings"): void {
  for (const t of ["tests", "chat", "settings"] as const) {
    $(`view-${t}`).hidden = t !== name;
    $(`tab-${t}`).classList.toggle("on", t === name);
  }
  if (name === "settings") void renderSettings();
  if (name === "chat") renderChat();
}

function init(): void {
  for (const t of ["tests", "chat", "settings"] as const) $(`tab-${t}`).addEventListener("click", () => showTab(t));
  $("send").addEventListener("click", () => void sendGoal());
  $("stop").addEventListener("click", () => void stopTask());
  $("goal").addEventListener("keydown", (e) => {
    if ((e as KeyboardEvent).key === "Enter") void sendGoal();
  });
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
