import {
  OPENROUTER_BASE_URL,
  ProviderError,
  recallHistory,
  verifyChain,
  type ChatMessage,
  type FetchLike,
  type StreamPart,
  type ToolSpec,
  type Usage,
} from "@open-computer/agent-core";
import defaults from "../../../contracts/defaults.json" with { type: "json" };
import type { Env } from "./env.ts";
import { buildProvider, gatewayConfigured, resolveTarget } from "./models/gateway.ts";
import { isValidationError, openRouterSdk, sdkStatus } from "./models/sdk.ts";
import { readSecret, SecretError } from "./models/secrets.ts";
import type { SandboxFactory } from "./sandbox/port.ts";
import { D1MemoryStore } from "./storage/d1-memory.ts";
import { ledgerStub, readAllAudit } from "./storage/ledger-client.ts";

/**
 * Diagnostics routes behind the setup wizard and `oc doctor`. Every route is authenticated by the handler before it
 * gets here, answers with small JSON, and never returns a key, a key label or an account id. Anything that costs
 * money goes through the same ledger reservation as a real task, so it counts toward the daily cap.
 */
export interface DiagDeps {
  fetch: FetchLike;
  sandbox: SandboxFactory;
  /** Effective daily cap in dollars, resolved by the handler (user setting held under any IT policy). */
  dayCap: number;
  now?: () => number;
}

const json = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

const round = (n: number, d = 1): number => Math.round(n * 10 ** d) / 10 ** d;
const today = (): string => new Date().toISOString().slice(0, 10);

export function percentile(sorted: number[], p: number): number {
  if (sorted.length === 0) return 0;
  const i = Math.min(sorted.length - 1, Math.max(0, Math.ceil((p / 100) * sorted.length) - 1));
  return sorted[i] as number;
}

async function timed<T>(now: () => number, fn: () => Promise<T>): Promise<{ value: T; ms: number }> {
  const t0 = now();
  const value = await fn();
  return { value, ms: now() - t0 };
}

function stats(samples: number[]): { n: number; p50: number; p95: number; min: number; max: number } {
  const s = [...samples].sort((a, b) => a - b);
  return { n: s.length, p50: round(percentile(s, 50)), p95: round(percentile(s, 95)), min: round(s[0] ?? 0), max: round(s[s.length - 1] ?? 0) };
}

/** Key-info call through the OpenRouter SDK. Only allowlisted numeric fields leave this function. */
async function keyInfo(env: Env, deps: DiagDeps): Promise<Response> {
  let key: string;
  try {
    key = await readSecret(env.MODEL_KEY, "model_key");
  } catch (e) {
    if (e instanceof SecretError) return json({ ok: false, code: "key_missing" });
    throw e;
  }
  const now = deps.now ?? (() => performance.now());
  const sdk = openRouterSdk(key, deps.fetch);
  const t0 = now();
  try {
    const res = await sdk.apiKeys.getCurrentKeyMetadata();
    const ms = now() - t0;
    const d = res.data;
    const n = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
    const limit = n(d.limit);
    const remaining = n(d.limitRemaining);
    const lowCredit = remaining !== null && remaining <= 0;
    return json({
      ok: !lowCredit,
      code: lowCredit ? "payment_required" : "ok",
      ms: round(ms),
      limit,
      limit_remaining: remaining,
      usage: n(d.usage),
      free_tier: d.isFreeTier === true,
    });
  } catch (e) {
    if (isValidationError(e)) return rawKeyInfo(key, deps, now);
    const ms = round(now() - t0);
    const status = sdkStatus(e);
    if (status === 401 || status === 403) return json({ ok: false, code: "key_invalid", ms });
    if (status === 402) return json({ ok: false, code: "payment_required", ms });
    if (status === 429) return json({ ok: false, code: "rate_limited", ms });
    return json({ ok: false, code: status === null ? "unreachable" : "error", ms });
  }
}

async function models(env: Env, deps: DiagDeps): Promise<Response> {
  const wanted = [defaults.default_model, defaults.worker_model];
  try {
    let key = "";
    try {
      key = await readSecret(env.MODEL_KEY, "model_key");
    } catch {
      key = "";
    }
    const sdk = openRouterSdk(key, deps.fetch);
    const found = new Map<string, { tools: boolean; reasoning: boolean }>();
    for await (const page of await sdk.models.list()) {
      for (const m of page.result.data) {
        if (wanted.includes(m.id)) {
          const params = (m.supportedParameters ?? []) as string[];
          found.set(m.id, { tools: params.includes("tools"), reasoning: params.includes("reasoning") || params.includes("include_reasoning") });
        }
      }
      if (found.size === wanted.length) break;
    }
    const out = wanted.map((id) => ({ id, available: found.has(id), tools: found.get(id)?.tools ?? false, reasoning: found.get(id)?.reasoning ?? false }));
    return json({ ok: out.every((m) => m.available), models: out });
  } catch (e) {
    if (isValidationError(e)) return rawModels(deps, wanted);
    return json({ ok: false, code: sdkStatus(e) === null ? "unreachable" : "error", models: [] });
  }
}

/** Raw HTTP versions, used only when the SDK's strict response schema rejects a response the API really sent. */
async function rawKeyInfo(key: string, deps: DiagDeps, now: () => number): Promise<Response> {
  const t0 = now();
  try {
    const res = await deps.fetch(`${OPENROUTER_BASE_URL}/key`, { method: "GET", headers: { authorization: `Bearer ${key}` }, signal: AbortSignal.timeout(8000) });
    const ms = round(now() - t0);
    if (res.status === 401 || res.status === 403) return json({ ok: false, code: "key_invalid", ms });
    if (res.status === 402) return json({ ok: false, code: "payment_required", ms });
    if (res.status === 429) return json({ ok: false, code: "rate_limited", ms });
    if (res.status < 200 || res.status >= 300) return json({ ok: false, code: "error", ms });
    const d = ((await res.json().catch(() => ({}))) as { data?: Record<string, unknown> }).data ?? {};
    const n = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
    const remaining = n(d.limit_remaining);
    const low = remaining !== null && remaining <= 0;
    return json({ ok: !low, code: low ? "payment_required" : "ok", ms, limit: n(d.limit), limit_remaining: remaining, usage: n(d.usage), free_tier: d.is_free_tier === true });
  } catch {
    return json({ ok: false, code: "unreachable" });
  }
}

async function rawModels(deps: DiagDeps, wanted: string[]): Promise<Response> {
  try {
    const res = await deps.fetch(`${OPENROUTER_BASE_URL}/models`, { method: "GET", headers: {}, signal: AbortSignal.timeout(10000) });
    if (!res.ok) return json({ ok: false, code: "error", models: [] });
    const list = ((await res.json()) as { data?: Array<{ id?: string; supported_parameters?: string[] }> }).data ?? [];
    const out = wanted.map((id) => {
      const m = list.find((x) => x.id === id);
      const params = m?.supported_parameters ?? [];
      return { id, available: !!m, tools: params.includes("tools"), reasoning: params.includes("reasoning") || params.includes("include_reasoning") };
    });
    return json({ ok: out.every((m) => m.available), models: out });
  } catch {
    return json({ ok: false, code: "unreachable", models: [] });
  }
}

const ECHO_TOOL: ToolSpec = {
  name: "diag_echo",
  description: "Returns the word it is given. Call it once with the word 'ping'.",
  parameters: { type: "object", properties: { word: { type: "string" } }, required: ["word"] },
  risk: "read",
};

/**
 * A tiny streamed completion with a reasoning budget and one tool call round trip. It reserves spend first, so the
 * daily cap holds, and settles to the provider-reported cost.
 */
async function completion(env: Env, deps: DiagDeps): Promise<Response> {
  const now = deps.now ?? (() => performance.now());
  const ledger = ledgerStub(env);
  const stepId = `diag:${crypto.randomUUID()}`;
  const estimate = 0.05;
  const reserved = await ledger.tryReserve({ taskId: "diag", stepId, day: today(), amount: estimate, taskCap: 1, dayCap: deps.dayCap });
  if (!reserved.ok) return json({ ok: false, code: "cap_reached", message: `The daily cap of $${deps.dayCap} is reached, so no test call was made.` });

  const provider = buildProvider(env, deps.fetch);
  const messages: ChatMessage[] = [
    { role: "system", content: "You are a connectivity test. Be extremely brief." },
    { role: "user", content: "Call the diag_echo tool with the word ping, then reply with one short sentence." },
  ];
  const t0 = now();
  let firstToken: number | null = null;
  let reasoningChars = 0;
  let textChars = 0;
  let toolCalls = 0;
  let cost = 0;
  let completionTokens = 0;
  let reasoningTokens = 0;
  const reasoningSample: string[] = [];
  const turns: Array<{ ms: number; finish: string | null }> = [];
  let streamStart = 0;
  try {
    for (let turn = 0; turn < 3; turn++) {
      const turnStart = now();
      let done: Extract<StreamPart, { t: "done" }> | null = null;
      for await (const part of provider({ model: defaults.default_model, messages, tools: [ECHO_TOOL], reasoning: { effort: "low" }, maxTokens: 400 })) {
        if (part.t === "error") {
          await ledger.settle({ stepId, actual: 0 });
          return json({ ok: false, code: part.code, message: part.message });
        }
        if (firstToken === null && (part.t === "text" || part.t === "reasoning" || part.t === "tool_start")) {
          firstToken = now() - t0;
          streamStart = now();
        }
        if (part.t === "reasoning") {
          reasoningChars += part.text.length;
          if (reasoningSample.join("").length < 160) reasoningSample.push(part.text);
        }
        if (part.t === "text") textChars += part.text.length;
        if (part.t === "done") done = part;
      }
      if (!done) break;
      const u: Usage | null = done.usage;
      if (u) {
        cost += u.cost ?? 0;
        completionTokens += u.completion;
        reasoningTokens += u.reasoning;
      }
      turns.push({ ms: round(now() - turnStart), finish: done.finishReason });
      messages.push(done.message);
      const calls = done.message.tool_calls ?? [];
      if (calls.length === 0) break;
      for (const c of calls) {
        toolCalls += 1;
        messages.push({ role: "tool", tool_call_id: c.id, content: "ping" });
      }
    }
  } catch (e) {
    await ledger.settle({ stepId, actual: 0 });
    const code = e instanceof ProviderError ? e.code : "error";
    return json({ ok: false, code, message: e instanceof ProviderError ? e.message : "The test call failed." });
  }
  const total = now() - t0;
  await ledger.settle({ stepId, actual: cost });
  await ledger.appendAudit("diag.model_call", "diag", { model: defaults.default_model, cost, via_gateway: gatewayConfigured(env) });
  const genMs = Math.max(1, now() - streamStart);
  return json({
    ok: toolCalls >= 1,
    code: toolCalls >= 1 ? "ok" : "no_tool_call",
    model: defaults.default_model,
    via_gateway: gatewayConfigured(env),
    ttft_ms: firstToken === null ? null : round(firstToken),
    total_ms: round(total),
    tokens_per_sec: completionTokens > 0 ? round((completionTokens / genMs) * 1000) : null,
    completion_tokens: completionTokens,
    reasoning_tokens: reasoningTokens,
    reasoning_chars: reasoningChars,
    reasoning_sample: reasoningSample.join("").slice(0, 160),
    text_chars: textChars,
    tool_calls: toolCalls,
    turns,
    cost_usd: round(cost, 6),
  });
}

async function perf(env: Env, deps: DiagDeps): Promise<Response> {
  const now = deps.now ?? (() => performance.now());
  const out: Record<string, unknown> = {};

  // D1 write and read latency, ten samples each, on a scratch row in the settings table that is removed after.
  const key = `diag:${crypto.randomUUID()}`;
  const writes: number[] = [];
  const reads: number[] = [];
  try {
    for (let i = 0; i < 10; i++) {
      writes.push(
        (await timed(now, () => env.DB.prepare("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value").bind(key, String(i)).run())).ms,
      );
      reads.push((await timed(now, () => env.DB.prepare("SELECT value FROM settings WHERE key = ?").bind(key).first())).ms);
    }
    out.d1_write_ms = stats(writes);
    out.d1_read_ms = stats(reads);
  } finally {
    await env.DB.prepare("DELETE FROM settings WHERE key = ?").bind(key).run();
  }

  // R2 put and get of 1 MiB, three rounds, then delete.
  const payload = new Uint8Array(1024 * 1024);
  crypto.getRandomValues(payload.subarray(0, 65536));
  const rkey = `diag/${crypto.randomUUID()}`;
  const puts: number[] = [];
  const gets: number[] = [];
  try {
    for (let i = 0; i < 3; i++) {
      puts.push((await timed(now, () => env.ARTIFACTS.put(rkey, payload))).ms);
      gets.push(
        (
          await timed(now, async () => {
            const obj = await env.ARTIFACTS.get(rkey);
            if (obj) await obj.arrayBuffer();
          })
        ).ms,
      );
    }
    out.r2_put_ms = stats(puts);
    out.r2_get_ms = stats(gets);
    out.r2_put_mib_per_s = round(1000 / Math.max(1, stats(puts).p50), 2);
    out.r2_get_mib_per_s = round(1000 / Math.max(1, stats(gets).p50), 2);
  } finally {
    await env.ARTIFACTS.delete(rkey);
  }

  // Edge to OpenRouter round trip (key-info), direct. With a gateway configured, also through the gateway: the
  // difference is the gateway overhead.
  let keyValue: string | null = null;
  try {
    keyValue = await readSecret(env.MODEL_KEY, "model_key");
  } catch {
    keyValue = null;
  }
  if (keyValue) {
    const direct: number[] = [];
    for (let i = 0; i < 5; i++) {
      try {
        direct.push((await timed(now, async () => (await deps.fetch(`${OPENROUTER_BASE_URL}/key`, { headers: { authorization: `Bearer ${keyValue}` }, signal: AbortSignal.timeout(8000) })).arrayBuffer())).ms);
      } catch {
        /* a failed sample is dropped */
      }
    }
    out.edge_to_openrouter_ms = stats(direct);
    const target = await resolveTarget(env);
    if (target.viaGateway) {
      const via: number[] = [];
      for (let i = 0; i < 5; i++) {
        try {
          via.push((await timed(now, async () => (await deps.fetch(`${target.baseUrl}/key`, { headers: { authorization: `Bearer ${keyValue}` }, signal: AbortSignal.timeout(8000) })).arrayBuffer())).ms);
        } catch {
          /* dropped */
        }
      }
      const g = stats(via);
      out.edge_to_openrouter_via_gateway_ms = g;
      out.gateway_overhead_ms = round(g.p50 - stats(direct).p50);
    } else {
      out.gateway_overhead_ms = null;
    }
  }
  return json({ ok: true, ...out });
}

async function sandbox(env: Env, deps: DiagDeps): Promise<Response> {
  const now = deps.now ?? (() => performance.now());
  const id = `diag-${crypto.randomUUID()}`;
  const box = await deps.sandbox(id);
  try {
    const cold = await timed(now, () => box.exec("echo oc-sandbox-ok", { timeoutMs: 120_000 }));
    if (cold.value.exitCode !== 0 || !cold.value.stdout.includes("oc-sandbox-ok")) {
      return json({ ok: false, code: "exec_failed", message: "The sandbox started but the test command did not run." });
    }
    const warm = await timed(now, () => box.exec("echo oc-sandbox-ok", { timeoutMs: 30_000 }));
    const envOut = await box.exec("env", { timeoutMs: 30_000 });
    let modelKey = "";
    try {
      modelKey = await readSecret(env.MODEL_KEY, "model_key");
    } catch {
      modelKey = "";
    }
    const accessSecret = await readSecret(env.ACCESS_SECRET, "access_secret").catch(() => "");
    const text = envOut.stdout;
    const names = text.split("\n").map((l) => l.split("=")[0] ?? "");
    const suspicious = names.filter((n) => /key|token|secret|password|credential/i.test(n));
    const leaked = (modelKey !== "" && text.includes(modelKey)) || (accessSecret !== "" && text.includes(accessSecret));
    // Egress: a request to the model host and to a public host must not succeed from inside the sandbox.
    const egress = await box.exec(
      "(curl -sS -m 6 -o /dev/null -w '%{http_code}' https://openrouter.ai/api/v1/models 2>&1 || true); echo; (curl -sS -m 6 -o /dev/null -w '%{http_code}' https://example.com 2>&1 || true)",
      { timeoutMs: 30_000 },
    );
    const lines = egress.stdout.split("\n").map((l) => l.trim());
    const reached = lines.some((l) => /^2\d\d$/.test(l));
    return json({
      ok: !leaked && suspicious.length === 0 && !reached,
      code: leaked ? "key_visible" : suspicious.length > 0 ? "key_shaped_variable" : reached ? "egress_open" : "ok",
      cold_ms: round(cold.ms),
      warm_ms: round(warm.ms),
      key_visible: leaked,
      key_shaped_variables: suspicious.length,
      egress_blocked: !reached,
    });
  } catch (e) {
    return json({ ok: false, code: "sandbox_unavailable", message: e instanceof Error ? e.message.slice(0, 200) : "The sandbox could not start." });
  } finally {
    await box.destroy().catch(() => undefined);
  }
}

async function memory(env: Env): Promise<Response> {
  const store = new D1MemoryStore(env.DB);
  const word = `diagword${crypto.randomUUID().replace(/-/g, "").slice(0, 10)}`;
  const id = `diag:${crypto.randomUUID()}`;
  const t0 = performance.now();
  await store.addHistory({ id, taskId: "diag", title: "Diagnostics note", summary: `The test phrase is ${word} and nothing else.`, ts: new Date().toISOString() });
  const found = await recallHistory(store, word, 3);
  const hit = found.find((r) => r.id === id);
  await store.deleteHistory(id);
  const after = await recallHistory(store, word, 3);
  const gone = !after.some((r) => r.id === id);
  return json({
    ok: !!hit && gone,
    code: !hit ? "recall_failed" : gone ? "ok" : "delete_failed",
    citation: hit?.source ?? null,
    ms: round(performance.now() - t0),
  });
}

async function audit(env: Env, deps: DiagDeps): Promise<Response> {
  const ledger = ledgerStub(env);
  const rec = (await ledger.appendAudit("diag.check", "diag", { note: "wizard audit probe" })) as unknown as { seq: number };
  const all = await readAllAudit(env);
  const chain = await verifyChain(all);
  const day = today();
  const [daySpent, diagSpent] = await Promise.all([
    ledger.daySpent(day),
    env.DB.prepare("SELECT step_id, amount FROM spend WHERE task_id = 'diag' AND day = ? ORDER BY rowid DESC LIMIT 5").bind(day).all<{ step_id: string; amount: number }>(),
  ]);
  return json({
    ok: chain.ok && all.some((r) => r.seq === rec.seq),
    code: chain.ok ? "ok" : "chain_broken",
    entries: all.length,
    head_seq: rec.seq,
    day_spent_usd: round(daySpent, 6),
    daily_cap_usd: deps.dayCap,
    diag_spend_rows: diagSpent.results.length,
    diag_spend_usd: round(diagSpent.results.reduce((s, r) => s + r.amount, 0), 6),
  });
}

/** SSE round trip: n small events, one every 100 ms, each stamped with its server send time. */
function stream(url: URL): Response {
  const n = Math.min(20, Math.max(1, Number(url.searchParams.get("n") ?? 10) || 10));
  const enc = new TextEncoder();
  let i = 0;
  const body = new ReadableStream<Uint8Array>({
    async pull(controller) {
      if (i >= n) {
        controller.close();
        return;
      }
      if (i > 0) await new Promise((r) => setTimeout(r, 100));
      controller.enqueue(enc.encode(`id: ${i}\ndata: ${JSON.stringify({ i, t: Date.now() })}\n\n`));
      i += 1;
    },
  });
  return new Response(body, { headers: { "content-type": "text/event-stream", "cache-control": "no-store" } });
}

async function bindings(env: Env, deps: DiagDeps, version: string): Promise<Response> {
  const probe = async (fn: () => Promise<unknown>): Promise<boolean> => {
    try {
      await fn();
      return true;
    } catch {
      return false;
    }
  };
  const [db, r2, modelKey] = await Promise.all([
    probe(() => env.DB.prepare("SELECT 1").first()),
    probe(() => env.ARTIFACTS.head("diag/none")),
    probe(() => readSecret(env.MODEL_KEY, "model_key")),
  ]);
  const target = await resolveTarget(env);
  return json({
    ok: db && r2,
    version,
    d1: db,
    r2,
    access_secret: true,
    model_key: modelKey,
    durable_objects: !!env.SESSION && !!env.LEDGER,
    workflow: !!env.TASK_WORKFLOW,
    container: !!env.SANDBOX,
    gateway: target.viaGateway,
    gateway_via: target.source ?? "direct",
    default_model: defaults.default_model,
    worker_model: defaults.worker_model,
    daily_cap_usd: deps.dayCap,
  });
}

export async function handleDiag(request: Request, env: Env, deps: DiagDeps, version: string): Promise<Response | null> {
  const url = new URL(request.url);
  const p = url.pathname;
  const m = request.method;
  if (!p.startsWith("/v1/diag/")) return null;
  if (m === "GET" && p === "/v1/diag/bindings") return bindings(env, deps, version);
  if (m === "GET" && p === "/v1/diag/stream") return stream(url);
  if (m === "GET" && p === "/v1/diag/openrouter/auth") return keyInfo(env, deps);
  if (m === "GET" && p === "/v1/diag/openrouter/models") return models(env, deps);
  if (m !== "POST") return null;
  if (p === "/v1/diag/openrouter/completion") return completion(env, deps);
  if (p === "/v1/diag/perf") return perf(env, deps);
  if (p === "/v1/diag/sandbox") return sandbox(env, deps);
  if (p === "/v1/diag/memory") return memory(env);
  if (p === "/v1/diag/audit") return audit(env, deps);
  return null;
}
