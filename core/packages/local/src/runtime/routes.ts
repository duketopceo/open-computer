import type { IncomingMessage, ServerResponse } from "node:http";
import { randomUUID } from "node:crypto";
import { mkdtemp, rm, writeFile, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import {
  OPENROUTER_BASE_URL,
  ProviderError,
  recallHistory,
  toNdjson,
  verifyChain,
  readAll,
  type ChatMessage,
  type StreamPart,
  type ToolSpec,
} from "@open-computer/agent-core";
import defaults from "../../../../contracts/defaults.json" with { type: "json" };
import { PolicyRefusal, type Runtime } from "./runtime.ts";

export interface RouteHelpers {
  send(res: ServerResponse, status: number, body: unknown): void;
  err(res: ServerResponse, status: number, code: string, message: string): void;
  readJson(req: IncomingMessage): Promise<unknown>;
}

export interface RuntimeRouteContext {
  runtime: Runtime;
  version: string;
  sandboxKind: string;
  modelKey: () => string | null;
  helpers: RouteHelpers;
}

const ID_RE = /^[A-Za-z0-9._-]{1,64}$/;
const KEY_FIELD = /key|secret|token|password|authorization|credential/i;
const round = (n: number, d = 1): number => Math.round(n * 10 ** d) / 10 ** d;
const today = (): string => new Date().toISOString().slice(0, 10);

function hasKeyField(v: unknown): boolean {
  if (Array.isArray(v)) return v.some(hasKeyField);
  if (v && typeof v === "object") return Object.entries(v as Record<string, unknown>).some(([k, x]) => KEY_FIELD.test(k) || hasKeyField(x));
  return false;
}

function stats(samples: number[]) {
  const s = [...samples].sort((a, b) => a - b);
  const pick = (p: number) => s[Math.min(s.length - 1, Math.max(0, Math.ceil((p / 100) * s.length) - 1))] ?? 0;
  return { n: s.length, p50: round(pick(50)), p95: round(pick(95)), min: round(s[0] ?? 0), max: round(s[s.length - 1] ?? 0) };
}

const ECHO_TOOL: ToolSpec = {
  name: "diag_echo",
  description: "Returns the word it is given. Call it once with the word 'ping'.",
  parameters: { type: "object", properties: { word: { type: "string" } }, required: ["word"] },
  risk: "read",
};

/** Handles the agent-runtime routes. Returns false when the path is not one of them. */
export async function handleRuntimeRoutes(c: RuntimeRouteContext, req: IncomingMessage, res: ServerResponse, p: string, method: string, url: URL): Promise<boolean> {
  const { runtime: rt, helpers: h } = c;
  const store = rt.store;

  if (method === "POST" && p === "/v1/tasks") {
    let body: Record<string, unknown>;
    try {
      body = (await h.readJson(req)) as Record<string, unknown>;
    } catch {
      h.err(res, 400, "bad_request", "The body must be JSON.");
      return true;
    }
    if (hasKeyField(body)) return h.err(res, 400, "keys_not_accepted", "Keys are never accepted in a task. Store them with the core."), true;
    if (typeof body.goal !== "string" || body.goal.trim() === "" || body.goal.length > 8000) return h.err(res, 400, "bad_request", "goal must be text of 1 to 8000 characters."), true;
    const sessionId = body.session_id === undefined ? randomUUID() : String(body.session_id);
    if (!ID_RE.test(sessionId)) return h.err(res, 400, "bad_request", "session_id may use letters, digits, dot, dash and underscore."), true;
    const taskCap = body.task_cap === undefined ? 1 : Number(body.task_cap);
    if (!Number.isFinite(taskCap) || taskCap <= 0 || taskCap > 100) return h.err(res, 400, "bad_request", "task_cap must be above 0 and at most 100."), true;
    const asked = body.day_cap === undefined || body.day_cap === null ? undefined : Number(body.day_cap);
    if (asked !== undefined && (!Number.isFinite(asked) || asked <= 0)) return h.err(res, 400, "bad_request", "day_cap must be above 0."), true;
    let started;
    try {
      started = await rt.startTask({ goal: body.goal, sessionId, taskCap, ...(asked !== undefined ? { dayCap: asked } : {}), ...(typeof body.model === "string" && body.model ? { model: body.model } : {}), memory: body.memory !== false });
    } catch (e) {
      if (e instanceof PolicyRefusal) return h.err(res, 403, "policy_model", e.message), true;
      return h.err(res, 500, "policy_invalid", `The IT policy cannot be read, so nothing runs until it is fixed: ${e instanceof Error ? e.message : "error"}`), true;
    }
    if ("busy" in started) return h.err(res, 409, "session_busy", "This session already has a task running. Wait for it to finish or start a new session."), true;
    h.send(res, 202, { task_id: started.taskId, session_id: started.sessionId, day_cap: started.dayCap });
    return true;
  }

  const m = /^\/v1\/sessions\/([^/]+)\/(.*)$/.exec(p);
  if (m) {
    const sessionId = m[1] as string;
    const rest = m[2] as string;
    if (!ID_RE.test(sessionId)) return h.err(res, 400, "bad_request", "Bad session id."), true;
    if (method === "GET" && rest === "events") {
      const last = Number(req.headers["last-event-id"] ?? 0) || 0;
      res.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-store", connection: "keep-alive" });
      res.write(": open\n\n");
      const off = rt.subscribe(sessionId, last, {
        send: (id, event) => void res.write(`id: ${id}\ndata: ${JSON.stringify(event)}\n\n`),
        close: () => void res.end(),
      });
      req.on("close", off);
      return true;
    }
    const approval = /^approvals\/([^/]+)$/.exec(rest);
    if (method === "POST" && approval) {
      let body: { hash?: unknown; approved?: unknown; note?: unknown };
      try {
        body = (await h.readJson(req)) as typeof body;
      } catch {
        return h.err(res, 400, "bad_request", "The body must be JSON."), true;
      }
      if (typeof body.hash !== "string" || typeof body.approved !== "boolean") return h.err(res, 400, "bad_request", "hash and approved are required."), true;
      if (body.note !== undefined && (typeof body.note !== "string" || body.note.length > 500)) return h.err(res, 400, "bad_request", "note must be text of at most 500 characters."), true;
      const r = rt.decide(approval[1] as string, { hash: body.hash, approved: body.approved });
      if (r.ok) {
        await rt.store.appendAudit("approval.decided", r.record.taskId, { approval_id: r.record.id, kind: r.record.kind, outcome: body.approved ? "approved" : "denied", ...(typeof body.note === "string" && body.note !== "" ? { note: body.note } : {}) });
        return h.send(res, 200, { ok: true, outcome: body.approved ? "approved" : "denied" }), true;
      }
      return h.err(res, r.reason === "unknown" ? 404 : 409, r.reason, `The approval was not recorded: ${r.reason.replace("_", " ")}.`), true;
    }
    const cancel = /^tasks\/([^/]+)\/cancel$/.exec(rest);
    if (method === "POST" && cancel) {
      rt.cancel(cancel[1] as string);
      return h.send(res, 200, { ok: true }), true;
    }
    return false;
  }

  // The routes the cloud core answers for Test connection, so local-only mode can check itself the same way.
  if (method === "GET" && p === "/v1/model/health") {
    const key = c.modelKey();
    if (!key) return h.send(res, 200, { ok: false, code: "key_missing" }), true;
    try {
      const r = await rt.opts.fetch(`${OPENROUTER_BASE_URL}/key`, { method: "GET", headers: { authorization: `Bearer ${key}` }, signal: AbortSignal.timeout(4000) });
      if (r.ok) return h.send(res, 200, { ok: true }), true;
      const code = r.status === 401 || r.status === 403 ? "key_invalid" : r.status === 402 ? "payment_required" : r.status === 429 ? "rate_limited" : "error";
      h.send(res, 200, { ok: false, code });
    } catch {
      h.send(res, 200, { ok: false, code: "error" });
    }
    return true;
  }
  if (method === "POST" && p === "/v1/memory/probe") {
    const id = `probe:${randomUUID()}`;
    try {
      await store.memory.addHistory({ id, taskId: "probe", title: "probe", summary: "probe note", ts: new Date().toISOString() });
      const back = (await store.memory.listHistory(50)).some((x) => x.id === id);
      await store.memory.deleteHistory(id);
      h.send(res, back ? 200 : 500, { ok: back });
    } catch {
      h.send(res, 500, { ok: false });
    }
    return true;
  }

  if (method === "GET" && p === "/v1/audit/export") {
    const records = await readAll(store.audit, 500);
    const chain = await verifyChain(records);
    const text = toNdjson(records);
    res.writeHead(200, { "content-type": "application/x-ndjson; charset=utf-8", "x-audit-chain": chain.ok ? "ok" : "broken" });
    res.end(text);
    return true;
  }

  if (!p.startsWith("/v1/diag/")) return false;
  const now = () => performance.now();

  if (method === "GET" && p === "/v1/diag/bindings") {
    const key = c.modelKey();
    const box = c.sandboxKind !== "none";
    h.send(res, 200, {
      ok: true,
      version: c.version,
      d1: true,
      r2: true,
      access_secret: true,
      model_key: key !== null,
      durable_objects: true,
      workflow: true,
      container: box,
      gateway: false,
      default_model: defaults.default_model,
      worker_model: defaults.worker_model,
      daily_cap_usd: rt.caps().effective,
      sandbox_kind: c.sandboxKind,
    });
    return true;
  }

  if (method === "GET" && p === "/v1/diag/stream") {
    const n = Math.min(20, Math.max(1, Number(url.searchParams.get("n") ?? 10) || 10));
    res.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-store" });
    for (let i = 0; i < n; i++) {
      if (i > 0) await new Promise((r) => setTimeout(r, 100));
      res.write(`id: ${i}\ndata: ${JSON.stringify({ i, t: Date.now() })}\n\n`);
    }
    res.end();
    return true;
  }

  if (method === "GET" && p === "/v1/diag/openrouter/auth") {
    const key = c.modelKey();
    if (!key) return h.send(res, 200, { ok: false, code: "key_missing" }), true;
    const t0 = now();
    try {
      const r = await rt.opts.fetch(`${OPENROUTER_BASE_URL}/key`, { method: "GET", headers: { authorization: `Bearer ${key}` }, signal: AbortSignal.timeout(8000) });
      const ms = round(now() - t0);
      if (r.status === 401 || r.status === 403) return h.send(res, 200, { ok: false, code: "key_invalid", ms }), true;
      if (r.status === 402) return h.send(res, 200, { ok: false, code: "payment_required", ms }), true;
      if (r.status === 429) return h.send(res, 200, { ok: false, code: "rate_limited", ms }), true;
      if (!r.ok) return h.send(res, 200, { ok: false, code: "error", ms }), true;
      const d = ((await r.json().catch(() => ({}))) as { data?: Record<string, unknown> }).data ?? {};
      const n = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
      const rem = n(d.limit_remaining);
      const low = rem !== null && rem <= 0;
      h.send(res, 200, { ok: !low, code: low ? "payment_required" : "ok", ms, limit: n(d.limit), limit_remaining: rem, usage: n(d.usage), free_tier: d.is_free_tier === true });
    } catch {
      h.send(res, 200, { ok: false, code: "unreachable" });
    }
    return true;
  }

  if (method === "GET" && p === "/v1/diag/openrouter/models") {
    const wanted = [defaults.default_model, defaults.worker_model];
    try {
      const r = await rt.opts.fetch(`${OPENROUTER_BASE_URL}/models`, { method: "GET", headers: {}, signal: AbortSignal.timeout(10000) });
      if (!r.ok) return h.send(res, 200, { ok: false, code: "error", models: [] }), true;
      const list = ((await r.json()) as { data?: Array<{ id?: string; supported_parameters?: string[] }> }).data ?? [];
      const out = wanted.map((id) => {
        const x = list.find((y) => y.id === id);
        const params = x?.supported_parameters ?? [];
        return { id, available: !!x, tools: params.includes("tools"), reasoning: params.includes("reasoning") || params.includes("include_reasoning") };
      });
      h.send(res, 200, { ok: out.every((x) => x.available), models: out });
    } catch {
      h.send(res, 200, { ok: false, code: "unreachable", models: [] });
    }
    return true;
  }

  if (method !== "POST") return false;

  if (p === "/v1/diag/openrouter/completion") {
    const stepId = `diag:${randomUUID()}`;
    const dayCap = rt.caps().effective;
    const reserved = await store.ledger.tryReserve({ taskId: "diag", stepId, day: today(), amount: 0.05, taskCap: 1, dayCap });
    if (!reserved.ok) return h.send(res, 200, { ok: false, code: "cap_reached", message: `The daily cap of $${dayCap} is reached, so no test call was made.` }), true;
    const provider = rt.provider();
    const messages: ChatMessage[] = [
      { role: "system", content: "You are a connectivity test. Be extremely brief." },
      { role: "user", content: "Call the diag_echo tool with the word ping, then reply with one short sentence." },
    ];
    const t0 = now();
    let first: number | null = null;
    let streamStart = 0;
    let cost = 0;
    let completion = 0;
    let reasoning = 0;
    let toolCalls = 0;
    let sample = "";
    try {
      for (let turn = 0; turn < 3; turn++) {
        let done: Extract<StreamPart, { t: "done" }> | null = null;
        for await (const part of provider({ model: defaults.default_model, messages, tools: [ECHO_TOOL], reasoning: { effort: "low" }, maxTokens: 400 })) {
          if (part.t === "error") {
            await store.ledger.settle({ stepId, actual: 0 });
            return h.send(res, 200, { ok: false, code: part.code, message: part.message }), true;
          }
          if (first === null && (part.t === "text" || part.t === "reasoning" || part.t === "tool_start")) {
            first = now() - t0;
            streamStart = now();
          }
          if (part.t === "reasoning" && sample.length < 160) sample += part.text;
          if (part.t === "done") done = part;
        }
        if (!done) break;
        if (done.usage) {
          cost += done.usage.cost ?? 0;
          completion += done.usage.completion;
          reasoning += done.usage.reasoning;
        }
        messages.push(done.message);
        const calls = done.message.tool_calls ?? [];
        if (calls.length === 0) break;
        for (const call of calls) {
          toolCalls += 1;
          messages.push({ role: "tool", tool_call_id: call.id, content: "ping" });
        }
      }
    } catch (e) {
      await store.ledger.settle({ stepId, actual: 0 });
      return h.send(res, 200, { ok: false, code: e instanceof ProviderError ? e.code : "error", message: e instanceof ProviderError ? e.message : "The test call failed." }), true;
    }
    await store.ledger.settle({ stepId, actual: cost });
    await store.appendAudit("diag.model_call", "diag", { model: defaults.default_model, cost });
    const genMs = Math.max(1, now() - streamStart);
    h.send(res, 200, {
      ok: toolCalls >= 1,
      code: toolCalls >= 1 ? "ok" : "no_tool_call",
      model: defaults.default_model,
      via_gateway: false,
      ttft_ms: first === null ? null : round(first),
      total_ms: round(now() - t0),
      tokens_per_sec: completion > 0 ? round((completion / genMs) * 1000) : null,
      completion_tokens: completion,
      reasoning_tokens: reasoning,
      reasoning_sample: sample.slice(0, 160),
      tool_calls: toolCalls,
      cost_usd: round(cost, 6),
    });
    return true;
  }

  if (p === "/v1/diag/perf") {
    const out: Record<string, unknown> = { ok: true };
    const key = `diag:${randomUUID()}`;
    const w: number[] = [];
    const r: number[] = [];
    for (let i = 0; i < 10; i++) {
      let t = now();
      store.setSetting(key, String(i));
      w.push(now() - t);
      t = now();
      store.getSetting(key);
      r.push(now() - t);
    }
    store.setSetting(key, null);
    out.d1_write_ms = stats(w);
    out.d1_read_ms = stats(r);
    const dir = await mkdtemp(path.join(tmpdir(), "oc-diag-"));
    const buf = Buffer.alloc(1024 * 1024, 7);
    const puts: number[] = [];
    const gets: number[] = [];
    try {
      for (let i = 0; i < 3; i++) {
        let t = now();
        await writeFile(path.join(dir, "f"), buf);
        puts.push(now() - t);
        t = now();
        await readFile(path.join(dir, "f"));
        gets.push(now() - t);
      }
    } finally {
      await rm(dir, { recursive: true, force: true });
    }
    out.r2_put_ms = stats(puts);
    out.r2_get_ms = stats(gets);
    out.r2_put_mib_per_s = round(1000 / Math.max(0.01, stats(puts).p50), 2);
    out.r2_get_mib_per_s = round(1000 / Math.max(0.01, stats(gets).p50), 2);
    const key2 = c.modelKey();
    if (key2) {
      const s: number[] = [];
      for (let i = 0; i < 5; i++) {
        try {
          const t = now();
          await (await rt.opts.fetch(`${OPENROUTER_BASE_URL}/key`, { headers: { authorization: `Bearer ${key2}` }, signal: AbortSignal.timeout(8000) })).arrayBuffer();
          s.push(now() - t);
        } catch {
          /* dropped */
        }
      }
      out.edge_to_openrouter_ms = stats(s);
    }
    out.gateway_overhead_ms = null;
    h.send(res, 200, out);
    return true;
  }

  if (p === "/v1/diag/sandbox") {
    let box;
    try {
      box = await rt.opts.sandbox(`diag-${randomUUID()}`);
    } catch (e) {
      return h.send(res, 200, { ok: false, code: "sandbox_unavailable", message: e instanceof Error ? e.message.slice(0, 200) : "The sandbox could not start." }), true;
    }
    try {
      const t0 = now();
      const cold = await box.exec("echo oc-sandbox-ok", { timeoutMs: 120_000 });
      const coldMs = now() - t0;
      if (cold.exitCode !== 0 || !cold.stdout.includes("oc-sandbox-ok")) return h.send(res, 200, { ok: false, code: "exec_failed", message: `The sandbox started but the test command did not run. ${cold.stderr.slice(0, 120)}` }), true;
      const t1 = now();
      await box.exec("echo oc-sandbox-ok", { timeoutMs: 30_000 });
      const warmMs = now() - t1;
      const envOut = await box.exec("env", { timeoutMs: 30_000 });
      const key = c.modelKey() ?? "";
      const names = envOut.stdout.split("\n").map((l) => l.split("=")[0] ?? "");
      const suspicious = names.filter((n) => /key|token|secret|password|credential/i.test(n));
      const leaked = key !== "" && envOut.stdout.includes(key);
      const egress = await box.exec("(curl -sS -m 6 -o /dev/null -w '%{http_code}' https://openrouter.ai/api/v1/models 2>&1 || true); echo; (curl -sS -m 6 -o /dev/null -w '%{http_code}' https://example.com 2>&1 || true)", { timeoutMs: 30_000 });
      const reached = egress.stdout.split("\n").some((l) => /^2\d\d$/.test(l.trim()));
      h.send(res, 200, {
        ok: !leaked && suspicious.length === 0 && !reached,
        code: leaked ? "key_visible" : suspicious.length > 0 ? "key_shaped_variable" : reached ? "egress_open" : "ok",
        cold_ms: round(coldMs),
        warm_ms: round(warmMs),
        key_visible: leaked,
        key_shaped_variables: suspicious.length,
        egress_blocked: !reached,
      });
    } catch (e) {
      h.send(res, 200, { ok: false, code: "sandbox_unavailable", message: e instanceof Error ? e.message.slice(0, 200) : "The sandbox failed." });
    } finally {
      await box.destroy().catch(() => undefined);
    }
    return true;
  }

  if (p === "/v1/diag/memory") {
    const word = `diagword${randomUUID().replace(/-/g, "").slice(0, 10)}`;
    const id = `diag:${randomUUID()}`;
    const t0 = now();
    await store.memory.addHistory({ id, taskId: "diag", title: "Diagnostics note", summary: `The test phrase is ${word} and nothing else.`, ts: new Date().toISOString() });
    const hit = (await recallHistory(store.memory, word, 3)).find((x) => x.id === id);
    await store.memory.deleteHistory(id);
    const gone = !(await recallHistory(store.memory, word, 3)).some((x) => x.id === id);
    h.send(res, 200, { ok: !!hit && gone, code: !hit ? "recall_failed" : gone ? "ok" : "delete_failed", citation: hit?.source ?? null, ms: round(now() - t0) });
    return true;
  }

  if (p === "/v1/diag/audit") {
    const rec = await store.appendAudit("diag.check", "diag", { note: "wizard audit probe" });
    const all = await readAll(store.audit, 500);
    const chain = await verifyChain(all);
    const day = today();
    const spent = await store.ledger.daySpent(day);
    const rows = store.db.prepare("SELECT amount FROM spend WHERE task_id = 'diag' AND day = ?").all(day) as Array<{ amount: number }>;
    h.send(res, 200, {
      ok: chain.ok && all.some((r) => r.seq === rec.seq),
      code: chain.ok ? "ok" : "chain_broken",
      entries: all.length,
      head_seq: rec.seq,
      day_spent_usd: round(spent, 6),
      daily_cap_usd: rt.caps().effective,
      diag_spend_rows: rows.length,
      diag_spend_usd: round(rows.reduce((s, r) => s + r.amount, 0), 6),
    });
    return true;
  }
  return false;
}
