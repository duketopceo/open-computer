import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { fromNdjson, verifyChain, type FetchLike, type SandboxPort } from "@open-computer/agent-core";
import { buildReport, runWizard, stepsFor } from "@open-computer/diagnostics";
import { startLocalServer, type RunningServer } from "../src/server.ts";
import { LocalStore } from "../src/runtime/store.ts";

const TOKEN = "daemon-token-for-tests";
const MODEL_KEY = ["sk", "test", "runtime1234567890"].join("-");

const open: Array<RunningServer | LocalStore> = [];
afterEach(async () => {
  while (open.length) {
    const x = open.pop();
    if (x && "close" in x && x.close.constructor.name === "AsyncFunction") await (x as RunningServer).close();
    else (x as LocalStore | undefined)?.close?.();
  }
});

const sse = (chunks: unknown[]): Response =>
  new Response(chunks.map((c) => `data: ${JSON.stringify(c)}\n\n`).join("") + "data: [DONE]\n\n", { headers: { "content-type": "text/event-stream" } });
const toolTurn = (name: string, args: string) =>
  sse([
    { choices: [{ delta: { reasoning: "thinking" } }] },
    { choices: [{ delta: { tool_calls: [{ index: 0, id: "c1", type: "function", function: { name, arguments: args } }] } }] },
    { choices: [{ delta: {}, finish_reason: "tool_calls" }], usage: { prompt_tokens: 10, completion_tokens: 6, cost: 0.002, completion_tokens_details: { reasoning_tokens: 3 } } },
  ]);
const textTurn = (t: string) =>
  sse([{ choices: [{ delta: { content: t } }] }, { choices: [{ delta: {}, finish_reason: "stop" }], usage: { prompt_tokens: 20, completion_tokens: 3, cost: 0.001 } }]);

interface Boot {
  base: string;
  store: LocalStore;
  chat: Array<() => Response>;
  boxes: Array<{ cmds: string[]; destroyed: boolean }>;
  call(method: string, p: string, body?: unknown, token?: string | null): Promise<Response>;
}

async function boot(opts: { sandboxEnv?: (cmd: string) => { stdout: string; exitCode?: number } } = {}): Promise<Boot> {
  const store = new LocalStore(":memory:");
  open.push(store);
  const chat: Array<() => Response> = [];
  const boxes: Boot["boxes"] = [];
  const dir = mkdtempSync(path.join(tmpdir(), "oc-key-"));
  const keyFile = path.join(dir, "key");
  writeFileSync(keyFile, MODEL_KEY);
  const fetchImpl: FetchLike = async (url, init) => {
    if (url.startsWith("http://127.0.0.1")) return fetch(url, init);
    if (url.endsWith("/chat/completions")) return (chat.shift() ?? (() => textTurn("done")))();
    if (url.endsWith("/key")) return new Response(JSON.stringify({ data: { limit: 10, usage: 1, limit_remaining: 9, is_free_tier: false } }));
    if (url.endsWith("/models")) return new Response(JSON.stringify({ data: [{ id: "z-ai/glm-5.3", pricing: { prompt: "0.0000006", completion: "0.0000022" }, supported_parameters: ["tools", "reasoning"] }, { id: "z-ai/glm-5.3-flash", pricing: { prompt: "0.0000001", completion: "0.0000004" }, supported_parameters: ["tools"] }] }));
    return new Response("{}", { status: 404 });
  };
  const sandbox = async (): Promise<SandboxPort> => {
    const box = { cmds: [] as string[], destroyed: false };
    boxes.push(box);
    return {
      exec: async (cmd) => {
        box.cmds.push(cmd);
        const r = opts.sandboxEnv?.(cmd);
        if (r) return { stdout: r.stdout, stderr: "", exitCode: r.exitCode ?? 0 };
        if (cmd === "env") return { stdout: "PATH=/usr/bin\nHOME=/workspace\n", stderr: "", exitCode: 0 };
        if (cmd.includes("curl")) return { stdout: "000\n000", stderr: "", exitCode: 0 };
        return { stdout: "oc-sandbox-ok\n", stderr: "", exitCode: 0 };
      },
      readFile: async () => "",
      writeFile: async () => {},
      destroy: async () => void (box.destroyed = true),
    };
  };
  const running = await startLocalServer({ token: TOKEN, fetch: fetchImpl, runtime: { store, sandbox, sandboxKind: "bubblewrap", modelKeyFile: keyFile } });
  open.push(running);
  const base = `http://127.0.0.1:${running.port}`;
  return {
    base,
    store,
    chat,
    boxes,
    call: (method, p, body, token = TOKEN) =>
      fetch(base + p, { method, headers: { ...(token ? { authorization: `Bearer ${token}` } : {}), "content-type": "application/json" }, ...(body !== undefined ? { body: JSON.stringify(body) } : {}) }),
  };
}

async function readEvents(b: Boot, sessionId: string, onEvent?: (e: Record<string, unknown>) => Promise<void>): Promise<Array<Record<string, unknown>>> {
  const res = await b.call("GET", `/v1/sessions/${sessionId}/events`);
  const reader = (res.body as ReadableStream<Uint8Array>).getReader();
  const dec = new TextDecoder();
  let buf = "";
  const out: Array<Record<string, unknown>> = [];
  for (;;) {
    const { value, done } = await reader.read();
    if (value) buf += dec.decode(value, { stream: true });
    const parts = buf.split("\n\n");
    buf = parts.pop() ?? "";
    for (const part of parts) {
      const data = part.split("\n").find((l) => l.startsWith("data:"));
      if (!data) continue;
      const e = JSON.parse(data.slice(5)) as Record<string, unknown>;
      out.push(e);
      await onEvent?.(e);
    }
    if (done) break;
  }
  return out;
}

describe("daemon auth and settings", () => {
  it("refuses every route without the token and never accepts a key in a task", async () => {
    const b = await boot();
    for (const [m, p] of [["GET", "/v1/health"], ["POST", "/v1/tasks"], ["GET", "/v1/diag/bindings"], ["GET", "/v1/audit/export"]] as const) {
      expect((await b.call(m, p, undefined, null)).status, p).toBe(401);
    }
    const res = await b.call("POST", "/v1/tasks", { goal: "g", api_key: "x" });
    expect(res.status).toBe(400);
  });

  it("holds the $5 default daily cap, lets the user change it, and enforces the maximum", async () => {
    const b = await boot();
    expect(await (await b.call("GET", "/v1/settings")).json()).toMatchObject({ effective_daily_cap_usd: 5, daily_cap_usd: null });
    await b.call("PUT", "/v1/settings", { daily_cap_usd: 2 });
    expect(await (await b.call("GET", "/v1/settings")).json()).toMatchObject({ effective_daily_cap_usd: 2 });
    expect((await b.call("PUT", "/v1/settings", { daily_cap_usd: 5000 })).status).toBe(400);
  });
});

describe("the full agent runtime", () => {
  it("runs a task: asks before exec, runs it in the sandbox after approval, records audit, spend and memory", async () => {
    const b = await boot();
    b.chat.push(() => toolTurn("sandbox.exec", '{"command":"echo hi"}'), () => textTurn("All done."));
    const started = (await (await b.call("POST", "/v1/tasks", { goal: "say hi from the sandbox" })).json()) as { task_id: string; session_id: string };
    let decided = false;
    const events = await readEvents(b, started.session_id, async (e) => {
      if (e.type === "approval" && e.state === "requested" && !decided) {
        decided = true;
        expect(b.boxes.flatMap((x) => x.cmds)).toEqual([]); // nothing ran before approval
        const r = await b.call("POST", `/v1/sessions/${started.session_id}/approvals/${e.approval_id}`, { hash: e.hash, approved: true });
        expect(r.status).toBe(200);
      }
    });
    expect(decided).toBe(true);
    expect(b.boxes[0]?.cmds).toEqual(["echo hi"]);
    expect(b.boxes[0]?.destroyed).toBe(true);
    expect(events.some((e) => e.type === "reasoning.delta" || e.type === "reasoning.done")).toBe(true);
    expect(events.some((e) => e.type === "message.done" && e.text === "All done.")).toBe(true);
    expect(events.at(-1)).toMatchObject({ type: "memory_write", tier: "history" });
    expect(events.some((e) => e.type === "run" && e.status === "done")).toBe(true);
    const spent = await b.store.ledger.taskSpent(started.task_id);
    expect(spent).toBeCloseTo(0.003, 6);
    const audit = fromNdjson(await (await b.call("GET", "/v1/audit/export")).text());
    expect(await verifyChain(audit)).toEqual({ ok: true });
    expect(audit.map((r) => r.kind)).toEqual(expect.arrayContaining(["approval.decided"]));
  });

  it("denies when the human says no, and a wrong hash leaves the task waiting", async () => {
    const b = await boot();
    b.chat.push(() => toolTurn("sandbox.exec", '{"command":"rm -rf x"}'), () => textTurn("understood"));
    const s = (await (await b.call("POST", "/v1/tasks", { goal: "dangerous" })).json()) as { session_id: string };
    let step = 0;
    const events = await readEvents(b, s.session_id, async (e) => {
      if (e.type === "approval" && e.state === "requested") {
        const bad = await b.call("POST", `/v1/sessions/${s.session_id}/approvals/${e.approval_id}`, { hash: "nope", approved: true });
        expect(bad.status).toBe(409);
        step += 1;
        await b.call("POST", `/v1/sessions/${s.session_id}/approvals/${e.approval_id}`, { hash: e.hash, approved: false });
      }
    });
    expect(step).toBe(1);
    expect(b.boxes.flatMap((x) => x.cmds)).toEqual([]);
    expect(events.some((e) => e.type === "run" && e.status === "done")).toBe(true);
  });

  it("replays only later events after Last-Event-ID and keeps two sessions apart", async () => {
    const b = await boot();
    b.chat.push(() => textTurn("one"));
    const s1 = (await (await b.call("POST", "/v1/tasks", { goal: "first" })).json()) as { session_id: string };
    const full = await readEvents(b, s1.session_id);
    b.chat.push(() => textTurn("two"));
    const s2 = (await (await b.call("POST", "/v1/tasks", { goal: "second" })).json()) as { session_id: string; task_id: string };
    const other = await readEvents(b, s2.session_id);
    expect(other.every((e) => e.task_id === s2.task_id)).toBe(true);
    const res = await fetch(`${b.base}/v1/sessions/${s1.session_id}/events`, { headers: { authorization: `Bearer ${TOKEN}`, "last-event-id": "3" } });
    const text = await res.text();
    const ids = [...text.matchAll(/^id: (\d+)$/gm)].map((m) => Number(m[1]));
    expect(ids.length).toBe(full.length - 3);
    expect(Math.min(...ids)).toBeGreaterThan(3);
  });

  it("refuses a second task in a busy session and ends tasks a dead process left behind", async () => {
    const b = await boot();
    b.store.insertTask({ taskId: "old", sessionId: "s", goal: "g" });
    expect((await b.call("POST", "/v1/tasks", { goal: "x", session_id: "s" })).status).toBe(409);
    expect(b.store.failOrphans()).toBe(1);
    expect(b.store.activeTaskInSession("s")).toBeNull();
  });

  it("uses real model prices, so a 5 cent task cap does not pause for approval", async () => {
    const b = await boot();
    b.chat.push(() => textTurn("ok"));
    const s = (await (await b.call("POST", "/v1/tasks", { goal: "say ok", task_cap: 0.05 })).json()) as { session_id: string };
    const events = await readEvents(b, s.session_id);
    expect(events.some((e) => e.type === "approval")).toBe(false);
    expect(events.some((e) => e.type === "run" && e.status === "done")).toBe(true);
  });
});

describe("the same test chain as the cloud core", () => {
  it("passes every applicable row against the daemon, with the Cloudflare rows not applicable", async () => {
    const b = await boot();
    b.chat.push(() => toolTurn("diag_echo", '{"word":"ping"}'), () => textTurn("pong"));
    let t = 0;
    const rows = await runWizard(
      stepsFor("self-hosted"),
      { baseUrl: b.base, token: TOKEN, appVersion: "0.1.0", backend: "self-hosted", fetch: (...a) => fetch(...a), now: () => (t += 1), pings: 4, cache: {} },
      () => {},
    );
    const failing = rows.filter((r) => r.status === "fail" || (r.status === "skip" && !r.notApplicable));
    expect(failing.map((r) => `${r.id}: ${r.detail}`)).toEqual([]);
    const report = buildReport(rows, { backend: "self-hosted", appVersion: "0.1.0" });
    expect(report).toMatchObject({ failed: 0, skipped: 0, not_applicable: 5 });
    expect(report.test_cost_usd).toBeCloseTo(0.003, 6);
    expect(JSON.stringify(rows)).not.toContain(MODEL_KEY);
    expect(JSON.stringify(rows)).not.toContain(TOKEN);
  });

  it("fails the sandbox rows when the key shows up inside, and when outbound works", async () => {
    const leak = await boot({ sandboxEnv: (cmd) => (cmd === "env" ? { stdout: `PATH=/bin\nX=${MODEL_KEY}\n` } : cmd.includes("curl") ? { stdout: "000\n000" } : { stdout: "oc-sandbox-ok\n" }) });
    const b1 = (await (await leak.call("POST", "/v1/diag/sandbox")).json()) as Record<string, unknown>;
    expect(b1).toMatchObject({ ok: false, code: "key_visible" });
    const open_ = await boot({ sandboxEnv: (cmd) => (cmd === "env" ? { stdout: "PATH=/bin\n" } : cmd.includes("curl") ? { stdout: "200\n200" } : { stdout: "oc-sandbox-ok\n" }) });
    expect(await (await open_.call("POST", "/v1/diag/sandbox")).json()).toMatchObject({ ok: false, code: "egress_open" });
  });

  it("says plainly that no sandbox is configured instead of crashing", async () => {
    const store = new LocalStore(":memory:");
    open.push(store);
    const running = await startLocalServer({
      token: TOKEN,
      runtime: { store, sandboxKind: "none", sandbox: async () => { throw new Error("No sandbox is configured, so commands cannot run on this core."); } },
    });
    open.push(running);
    const r = await fetch(`http://127.0.0.1:${running.port}/v1/diag/sandbox`, { method: "POST", headers: { authorization: `Bearer ${TOKEN}` } });
    expect(await r.json()).toMatchObject({ ok: false, code: "sandbox_unavailable" });
  });
});

describe("local-only mode checks itself", () => {
  it("answers Test connection with no cloud address, using its own model and memory routes", async () => {
    const b = await boot();
    expect((await b.call("PUT", "/v1/settings", { cloud_url: "" })).status).toBe(200);
    const r = (await (await b.call("POST", "/v1/test-connection")).json()) as { all_ok: boolean; backend: { ok: boolean }; model: { ok: boolean }; memory: { ok: boolean } };
    expect(r).toMatchObject({ all_ok: true, backend: { ok: true }, model: { ok: true }, memory: { ok: true } });
  });
});
