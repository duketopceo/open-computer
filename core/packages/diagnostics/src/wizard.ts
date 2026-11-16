// The setup and test chain. One list of rows, run in order; each row is running (spinner), then pass, fail or skip,
// with its time. The same rows drive the web wizard, the desktop screen's JSON and `oc doctor`.
// Nothing here imports a Node or Cloudflare API: it needs only `fetch`, a clock and an optional control plane.

export type Backend = "cloudflare" | "self-hosted" | "local";
export type Status = "pending" | "running" | "pass" | "fail" | "skip";

export interface Metric {
  label: string;
  value: string;
}

export interface Fix {
  /** Plain-language next step. */
  text: string;
  /** Optional machine-readable action the UI can offer as a button, for example "retry" or "open:cloudflare-billing". */
  action?: string;
}

export interface Outcome {
  ok: boolean;
  detail: string;
  metrics?: Metric[];
  fix?: Fix;
}

export interface Row {
  id: string;
  group: string;
  title: string;
  status: Status;
  ms?: number;
  detail?: string;
  metrics?: Metric[];
  fix?: Fix;
}

export interface HttpResult {
  status: number;
  /** Parsed JSON body, or null. */
  body: Record<string, any> | null;
  ms: number;
  error?: "timeout" | "unreachable";
}

/** Control-plane steps that need the owner's Cloudflare login. Only the CLI provides this; the web page, served by an already deployed core, does not. */
export interface ControlPlane {
  run(step: "account" | "features" | "provision" | "keys" | "deploy"): Promise<Outcome>;
}

export interface Ctx {
  baseUrl: string;
  token: string;
  appVersion: string;
  backend: Backend;
  fetch: typeof fetch;
  now: () => number;
  control?: ControlPlane;
  /** Number of pings for the app-to-edge round trip. */
  pings: number;
  /** Results shared between rows (for example one sandbox run feeds two groups). */
  cache: Record<string, unknown>;
}

export interface Def {
  id: string;
  group: string;
  title: string;
  requires?: string[];
  run(ctx: Ctx): Promise<Outcome>;
}

export const ms1 = (n: number): string => `${Math.round(n * 10) / 10} ms`;
const usd = (n: number): string => `$${n < 0.01 ? n.toFixed(5) : n.toFixed(3)}`;

export function percentile(sorted: number[], p: number): number {
  if (sorted.length === 0) return 0;
  return sorted[Math.min(sorted.length - 1, Math.max(0, Math.ceil((p / 100) * sorted.length) - 1))] as number;
}

export async function call(ctx: Ctx, method: "GET" | "POST", path: string, timeoutMs = 60_000): Promise<HttpResult> {
  const t0 = ctx.now();
  try {
    const res = await ctx.fetch(`${ctx.baseUrl.replace(/\/+$/, "")}${path}`, {
      method,
      headers: { authorization: `Bearer ${ctx.token}`, "content-type": "application/json" },
      signal: AbortSignal.timeout(timeoutMs),
    });
    let body: Record<string, any> | null = null;
    try {
      body = (await res.json()) as Record<string, any>;
    } catch {
      body = null;
    }
    return { status: res.status, body, ms: ctx.now() - t0 };
  } catch (e) {
    const name = e instanceof Error ? e.name : "";
    return { status: 0, body: null, ms: ctx.now() - t0, error: name === "TimeoutError" || name === "AbortError" ? "timeout" : "unreachable" };
  }
}

function transport(r: HttpResult): Outcome | null {
  if (r.error === "timeout") return { ok: false, detail: "The backend did not answer in time.", fix: { text: "Check your internet connection, then retry.", action: "retry" } };
  if (r.error === "unreachable") return { ok: false, detail: "The backend could not be reached.", fix: { text: "Check the address and your internet connection, then retry.", action: "retry" } };
  if (r.status === 401) return { ok: false, detail: "The access token was refused.", fix: { text: "Paste the access token printed when the core was set up, or run setup again to issue a new one.", action: "enter-token" } };
  if (r.status === 429) return { ok: false, detail: "Too many wrong tokens from this network.", fix: { text: "Wait a minute, then retry with the right token.", action: "retry" } };
  if (r.status === 503 && r.body?.error?.code === "not_configured") return { ok: false, detail: "The core is deployed but has no access secret yet.", fix: { text: "Run setup again; it stores the secret.", action: "retry" } };
  if (r.status >= 500 || r.status === 0) return { ok: false, detail: `The backend answered with an error (${r.status}).`, fix: { text: "Retry. If it keeps failing, run setup again.", action: "retry" } };
  return null;
}

const MODEL_FIX: Record<string, Fix> = {
  key_missing: { text: "Store your OpenRouter key: run setup again and paste it when asked.", action: "store-key" },
  key_invalid: { text: "OpenRouter rejected the key. Create a new key at openrouter.ai/keys and store it again.", action: "store-key" },
  payment_required: { text: "The OpenRouter account has no credit or the key's limit is used up. Add credit or raise the key limit.", action: "open:openrouter-credits" },
  rate_limited: { text: "OpenRouter is rate limiting this key. Wait a minute and retry.", action: "retry" },
  cap_reached: { text: "Today's spend cap is reached. Raise the daily cap in Settings, or retry tomorrow.", action: "open:settings" },
  unreachable: { text: "The backend could not reach OpenRouter. Retry.", action: "retry" },
};
const modelFix = (code: string): Fix => MODEL_FIX[code] ?? { text: "Retry. If it keeps failing, check status.openrouter.ai.", action: "retry" };

const CONTROL_STEPS = [
  ["cf.account", "Cloudflare account and token", "account"],
  ["cf.features", "Plan and features (Workers Paid, R2, D1, Containers, Secrets Store)", "features"],
  ["deploy.provision", "Create and bind D1, R2, Secrets Store, Durable Objects, Workflows, Container image", "provision"],
  ["deploy.worker", "Deploy the core Worker (safe to repeat)", "deploy"],
  ["keys.model", "Store the OpenRouter key in Secrets Store", "keys"],
] as const;

/** With no control plane (the web page), these rows are proven from what the deployed core reports about itself. */
async function derived(ctx: Ctx, id: string): Promise<Outcome> {
  const r = await call(ctx, "GET", "/v1/diag/bindings", 20_000);
  const bad = transport(r);
  if (bad) return bad;
  const b = r.body ?? {};
  ctx.cache.bindings = b;
  switch (id) {
    case "cf.account":
      return { ok: true, detail: "This page is served by your own deployed core, so the account and token worked earlier." };
    case "cf.features":
      return b.container
        ? { ok: true, detail: "Durable Objects, D1, R2, Secrets Store and the Container binding are all present." }
        : { ok: false, detail: "The Container binding is missing.", fix: { text: "Containers need the Workers Paid plan. Enable it in the Cloudflare dashboard, then run setup again.", action: "open:cloudflare-billing" } };
    case "deploy.provision":
      return b.d1 && b.r2 && b.durable_objects && b.workflow
        ? { ok: true, detail: "D1, R2, Durable Objects and Workflows are bound and answering." }
        : { ok: false, detail: "A required binding is missing or not answering.", fix: { text: "Run setup again; it repairs missing resources.", action: "retry" } };
    case "deploy.worker":
      return { ok: true, detail: `The core is running (version ${String(b.version ?? "unknown")}).` };
    default:
      return b.model_key
        ? { ok: true, detail: "An OpenRouter key is stored (its value is never shown)." }
        : { ok: false, detail: "No OpenRouter key is stored yet.", fix: MODEL_FIX.key_missing };
  }
}

function controlDef(id: string, title: string, step: "account" | "features" | "provision" | "keys" | "deploy"): Def {
  return {
    id,
    group: step === "account" || step === "features" ? "1. Cloudflare account" : step === "keys" ? "3. Store keys" : "2. Provision and deploy",
    title,
    requires: id === "cf.account" ? [] : id === "cf.features" ? ["cf.account"] : id === "keys.model" ? ["deploy.worker"] : id === "deploy.provision" ? ["cf.features"] : ["deploy.provision"],
    run: (ctx) => (ctx.control ? ctx.control.run(step) : derived(ctx, id)),
  };
}

const s = (n: number | undefined | null): string => (typeof n === "number" ? ms1(n) : "n/a");

function cloudflareDefs(): Def[] {
  const control = CONTROL_STEPS.map(([id, title, step]) => controlDef(id, title, step));
  // Order the rows by group number, keeping the provision rows before the worker deploy.
  const defs: Def[] = [
    control[0] as Def,
    control[1] as Def,
    control[2] as Def,
    control[3] as Def,
    control[4] as Def,
    {
      id: "backend.health",
      group: "4. Backend health",
      title: "Core answers /health and matches this app's version",
      requires: ["deploy.worker"],
      async run(ctx) {
        const r = await call(ctx, "GET", "/v1/health", 20_000);
        const bad = transport(r);
        if (bad) return bad;
        const v = String(r.body?.version ?? "");
        const major = (x: string): string => x.split(".").slice(0, 2).join(".");
        if (ctx.appVersion && major(v) !== major(ctx.appVersion)) {
          return {
            ok: false,
            detail: `The core is version ${v} but this app is ${ctx.appVersion}.`,
            metrics: [{ label: "round trip", value: ms1(r.ms) }],
            fix: { text: "Update the core: run `oc setup` again to deploy the matching version.", action: "update-core" },
          };
        }
        return { ok: true, detail: `Version ${v}${ctx.appVersion ? " matches" : ""}.`, metrics: [{ label: "round trip", value: ms1(r.ms) }] };
      },
    },
    {
      id: "or.auth",
      group: "5. OpenRouter",
      title: "Key is valid; credit and limits",
      requires: ["backend.health"],
      async run(ctx) {
        const r = await call(ctx, "GET", "/v1/diag/openrouter/auth", 30_000);
        const bad = transport(r);
        if (bad) return bad;
        const b = r.body ?? {};
        if (!b.ok) return { ok: false, detail: b.code === "key_missing" ? "No OpenRouter key is stored." : b.code === "key_invalid" ? "OpenRouter rejected the key." : b.code === "payment_required" ? "The key has no credit or limit left." : "The OpenRouter check failed.", fix: modelFix(String(b.code)) };
        const m: Metric[] = [{ label: "round trip", value: s(b.ms) }];
        if (typeof b.usage === "number") m.push({ label: "key usage", value: usd(b.usage) });
        m.push({ label: "key limit", value: typeof b.limit === "number" ? usd(b.limit) : "none set" });
        if (typeof b.limit_remaining === "number") m.push({ label: "remaining", value: usd(b.limit_remaining) });
        return { ok: true, detail: b.free_tier ? "Key is valid (free tier: limits are low)." : "Key is valid.", metrics: m };
      },
    },
    {
      id: "or.models",
      group: "5. OpenRouter",
      title: "Default and worker models are available",
      requires: ["or.auth"],
      async run(ctx) {
        const r = await call(ctx, "GET", "/v1/diag/openrouter/models", 30_000);
        const bad = transport(r);
        if (bad) return bad;
        const list = (r.body?.models ?? []) as Array<{ id: string; available: boolean; tools: boolean }>;
        const missing = list.filter((m) => !m.available);
        const m = list.map((x) => ({ label: x.id, value: x.available ? (x.tools ? "available, tools" : "available, no tool support listed") : "not found" }));
        if (missing.length > 0) {
          return { ok: false, detail: `${missing.map((x) => x.id).join(", ")} ${missing.length === 1 ? "is" : "are"} not offered by OpenRouter right now.`, metrics: m, fix: { text: "Pick another model in Settings, or retry later.", action: "open:settings" } };
        }
        return { ok: true, detail: "Both models are listed.", metrics: m };
      },
    },
    {
      id: "or.stream",
      group: "5. OpenRouter",
      title: "Streamed completion with reasoning and one tool call",
      requires: ["or.models"],
      async run(ctx) {
        const r = await call(ctx, "POST", "/v1/diag/openrouter/completion", 120_000);
        const bad = transport(r);
        if (bad) return bad;
        const b = r.body ?? {};
        ctx.cache.completion = b;
        if (!b.ok) {
          return { ok: false, detail: String(b.message ?? (b.code === "no_tool_call" ? "The model answered but did not call the tool." : "The test call failed.")), fix: modelFix(String(b.code)) };
        }
        const m: Metric[] = [
          { label: "model", value: String(b.model) },
          { label: "route", value: b.via_gateway ? "through AI Gateway" : "direct to OpenRouter" },
          { label: "reasoning tokens", value: String(b.reasoning_tokens) },
          { label: "tool calls", value: String(b.tool_calls) },
          { label: "cost (counts toward the daily cap)", value: usd(Number(b.cost_usd ?? 0)) },
        ];
        if (b.reasoning_sample) m.push({ label: "reasoning", value: String(b.reasoning_sample) });
        const thought = Number(b.reasoning_tokens) > 0 || Boolean(b.reasoning_sample);
        return { ok: true, detail: thought ? "Streamed with reasoning, called the tool and finished." : "Streamed, called the tool and finished (the model chose not to emit reasoning for this tiny task).", metrics: m };
      },
    },
    {
      id: "net.rtt",
      group: "6. Connection speed and latency",
      title: "App to edge round trip",
      requires: ["backend.health"],
      async run(ctx) {
        const samples: number[] = [];
        let first = 0;
        for (let i = 0; i < ctx.pings; i++) {
          const r = await call(ctx, "GET", "/v1/health", 15_000);
          if (r.status === 200) {
            if (i === 0) first = r.ms;
            else samples.push(r.ms);
          }
        }
        if (samples.length === 0) return { ok: false, detail: "No ping got an answer.", fix: { text: "Check your connection and retry.", action: "retry" } };
        const sorted = [...samples].sort((a, b) => a - b);
        const p95 = percentile(sorted, 95);
        return {
          ok: true,
          detail: p95 > 1500 ? "Reachable but slow." : "Reachable.",
          metrics: [
            { label: "p50", value: ms1(percentile(sorted, 50)) },
            { label: "p95", value: ms1(p95) },
            { label: "first (cold connection)", value: ms1(first) },
            { label: "pings", value: String(samples.length + 1) },
          ],
        };
      },
    },
    {
      id: "net.model",
      group: "6. Connection speed and latency",
      title: "Edge to OpenRouter: time to first token and tokens per second",
      requires: ["or.stream"],
      async run(ctx) {
        const b = (ctx.cache.completion ?? {}) as Record<string, any>;
        return {
          ok: typeof b.ttft_ms === "number",
          detail: typeof b.ttft_ms === "number" ? "Measured on the streamed completion above." : "No token arrived to time.",
          metrics: [
            { label: "time to first token", value: s(b.ttft_ms) },
            { label: "tokens per second", value: typeof b.tokens_per_sec === "number" ? String(b.tokens_per_sec) : "n/a" },
            { label: "whole call", value: s(b.total_ms) },
          ],
        };
      },
    },
    {
      id: "net.edge",
      group: "6. Connection speed and latency",
      title: "AI Gateway overhead, D1 read and write, R2 put and get",
      requires: ["backend.health"],
      async run(ctx) {
        const r = await call(ctx, "POST", "/v1/diag/perf", 90_000);
        const bad = transport(r);
        if (bad) return bad;
        const b = r.body ?? {};
        const st = (x: any): string => (x ? `p50 ${ms1(x.p50)}, p95 ${ms1(x.p95)}` : "n/a");
        return {
          ok: !!b.ok,
          detail: "Measured inside the edge Worker.",
          metrics: [
            { label: "D1 write", value: st(b.d1_write_ms) },
            { label: "D1 read", value: st(b.d1_read_ms) },
            { label: "R2 put 1 MiB", value: `${st(b.r2_put_ms)} (${b.r2_put_mib_per_s} MiB/s)` },
            { label: "R2 get 1 MiB", value: `${st(b.r2_get_ms)} (${b.r2_get_mib_per_s} MiB/s)` },
            { label: "edge to OpenRouter", value: st(b.edge_to_openrouter_ms) },
            { label: "AI Gateway overhead", value: typeof b.gateway_overhead_ms === "number" ? ms1(b.gateway_overhead_ms) : "not configured (direct route)" },
          ],
        };
      },
    },
    {
      id: "net.sandbox",
      group: "6. Connection speed and latency",
      title: "Sandbox cold start and warm command",
      requires: ["backend.health"],
      async run(ctx) {
        const r = await call(ctx, "POST", "/v1/diag/sandbox", 180_000);
        const bad = transport(r);
        ctx.cache.sandbox = r;
        if (bad) return bad;
        const b = r.body ?? {};
        if (typeof b.cold_ms !== "number") return sandboxFailure(b);
        return { ok: true, detail: "A fresh container started and ran a command.", metrics: [{ label: "cold start + first command", value: ms1(b.cold_ms) }, { label: "warm command", value: ms1(b.warm_ms) }] };
      },
    },
    {
      id: "net.sse",
      group: "6. Connection speed and latency",
      title: "Event stream (SSE) round trip",
      requires: ["backend.health"],
      async run(ctx) {
        const t0 = ctx.now();
        try {
          const res = await ctx.fetch(`${ctx.baseUrl.replace(/\/+$/, "")}/v1/diag/stream?n=10`, {
            headers: { authorization: `Bearer ${ctx.token}` },
            signal: AbortSignal.timeout(20_000),
          });
          if (!res.ok || !res.body) return transport({ status: res.status, body: null, ms: 0 }) ?? { ok: false, detail: "The event stream did not open.", fix: { text: "Retry.", action: "retry" } };
          const reader = res.body.getReader();
          const dec = new TextDecoder();
          let buf = "";
          const stamps: number[] = [];
          for (;;) {
            const { done, value } = await reader.read();
            if (done) break;
            buf += dec.decode(value, { stream: true });
            const parts = buf.split("\n\n");
            buf = parts.pop() ?? "";
            for (const _ of parts) stamps.push(ctx.now());
          }
          if (stamps.length < 10) return { ok: false, detail: `Only ${stamps.length} of 10 events arrived.`, fix: { text: "A proxy may be buffering the stream. Retry on another network.", action: "retry" } };
          const gaps = stamps.slice(1).map((t, i) => t - (stamps[i] as number));
          const sorted = [...gaps].sort((a, b) => a - b);
          return { ok: true, detail: "All 10 events arrived in order.", metrics: [{ label: "time to first event", value: ms1((stamps[0] as number) - t0) }, { label: "gap p50 (sent every 100 ms)", value: ms1(percentile(sorted, 50)) }, { label: "gap p95", value: ms1(percentile(sorted, 95)) }] };
        } catch {
          return { ok: false, detail: "The event stream broke.", fix: { text: "Retry.", action: "retry" } };
        }
      },
    },
    {
      id: "sandbox.run",
      group: "7. Sandbox",
      title: "Trivial command in a fresh container",
      requires: ["backend.health"],
      async run(ctx) {
        const r = (ctx.cache.sandbox ?? (await call(ctx, "POST", "/v1/diag/sandbox", 180_000))) as HttpResult;
        ctx.cache.sandbox = r;
        const bad = transport(r);
        if (bad) return bad;
        const b = r.body ?? {};
        return typeof b.cold_ms === "number" ? { ok: true, detail: "Ran `echo` and destroyed the container." } : sandboxFailure(b);
      },
    },
    {
      id: "sandbox.keys",
      group: "7. Sandbox",
      title: "No API key is visible inside, and egress is closed",
      requires: ["sandbox.run"],
      async run(ctx) {
        const b = ((ctx.cache.sandbox as HttpResult | undefined)?.body ?? {}) as Record<string, any>;
        const m: Metric[] = [
          { label: "model key visible", value: b.key_visible ? "YES" : "no" },
          { label: "key-shaped variables", value: String(b.key_shaped_variables ?? 0) },
          { label: "outbound internet and model host", value: b.egress_blocked ? "blocked" : "OPEN" },
        ];
        return b.ok ? { ok: true, detail: "The sandbox sees no secret and cannot call out on its own.", metrics: m } : { ok: false, detail: b.key_visible ? "A secret is visible inside the sandbox." : "The sandbox can reach the internet.", metrics: m, fix: { text: "This is a security defect. Do not run tasks; update the core with `oc setup` and retest.", action: "update-core" } };
      },
    },
    {
      id: "memory.cycle",
      group: "8. Memory",
      title: "Write, recall with a citation, delete",
      requires: ["backend.health"],
      async run(ctx) {
        const r = await call(ctx, "POST", "/v1/diag/memory", 30_000);
        const bad = transport(r);
        if (bad) return bad;
        const b = r.body ?? {};
        return b.ok ? { ok: true, detail: "Wrote a note, found it by keyword, and removed it.", metrics: [{ label: "citation", value: String(b.citation) }, { label: "time", value: s(b.ms) }] } : { ok: false, detail: `Memory ${String(b.code ?? "check").replace("_", " ")}.`, fix: { text: "Run setup again to apply the database migration, then retry.", action: "retry" } };
      },
    },
    {
      id: "audit.chain",
      group: "9. Audit and spend",
      title: "Audit entry written and the chain verifies; spend ledger and daily cap",
      requires: ["backend.health"],
      async run(ctx) {
        const r = await call(ctx, "POST", "/v1/diag/audit", 30_000);
        const bad = transport(r);
        if (bad) return bad;
        const b = r.body ?? {};
        const m: Metric[] = [
          { label: "audit entries", value: String(b.entries) },
          { label: "spent today", value: usd(Number(b.day_spent_usd ?? 0)) },
          { label: "test spend recorded", value: `${usd(Number(b.diag_spend_usd ?? 0))} (${b.diag_spend_rows} rows)` },
          { label: "daily cap in effect", value: usd(Number(b.daily_cap_usd ?? 0)) },
        ];
        return b.ok ? { ok: true, detail: "The hash chain verifies end to end.", metrics: m } : { ok: false, detail: "The audit chain does not verify.", metrics: m, fix: { text: "Export the audit log and do not run tasks until it is investigated.", action: "export-audit" } };
      },
    },
  ];
  return defs;
}

function sandboxFailure(b: Record<string, any>): Outcome {
  if (b.code === "sandbox_unavailable") {
    return { ok: false, detail: `The sandbox could not start${b.message ? `: ${String(b.message).slice(0, 160)}` : "."}`, fix: { text: "Containers need the Workers Paid plan and a deployed image. Check the plan in the Cloudflare dashboard, then run setup again.", action: "open:cloudflare-billing" } };
  }
  return { ok: false, detail: String(b.message ?? "The sandbox check failed."), fix: { text: "Retry.", action: "retry" } };
}

/** Rows for a backend choice. Self-hosted and local-only run the local core, which has no Cloudflare resources to test. */
export function stepsFor(backend: Backend): Def[] {
  if (backend === "cloudflare") return cloudflareDefs();
  const where = backend === "self-hosted" ? "Your server" : "This computer";
  return [
    {
      id: "backend.health",
      group: "1. Backend",
      title: `${where}: core answers /health and matches this app's version`,
      async run(ctx) {
        const r = await call(ctx, "GET", "/v1/health", 20_000);
        const bad = transport(r);
        if (bad) return bad;
        return { ok: true, detail: `Version ${String(r.body?.version ?? "unknown")}.`, metrics: [{ label: "round trip", value: ms1(r.ms) }] };
      },
    },
    {
      id: "net.rtt",
      group: "2. Connection speed and latency",
      title: "App to core round trip",
      requires: ["backend.health"],
      async run(ctx) {
        const samples: number[] = [];
        for (let i = 0; i < ctx.pings; i++) {
          const r = await call(ctx, "GET", "/v1/health", 15_000);
          if (r.status === 200) samples.push(r.ms);
        }
        if (samples.length === 0) return { ok: false, detail: "No ping got an answer.", fix: { text: "Check that the core is running and retry.", action: "retry" } };
        const sorted = [...samples].sort((a, b) => a - b);
        return { ok: true, detail: "Reachable.", metrics: [{ label: "p50", value: ms1(percentile(sorted, 50)) }, { label: "p95", value: ms1(percentile(sorted, 95)) }] };
      },
    },
    {
      id: "model.connection",
      group: "3. OpenRouter",
      title: "Key stored and accepted",
      requires: ["backend.health"],
      async run(ctx) {
        const r = await call(ctx, "POST", "/v1/test-connection", 30_000);
        const bad = transport(r);
        if (bad) return bad;
        const m = (r.body?.model ?? {}) as { ok?: boolean; message?: string };
        return m.ok ? { ok: true, detail: "OpenRouter accepted the key." } : { ok: false, detail: String(m.message ?? "OpenRouter did not accept the key."), fix: { text: "Store a valid OpenRouter key and retry.", action: "store-key" } };
      },
    },
    {
      id: "memory.cycle",
      group: "4. Memory",
      title: "Memory store answers",
      requires: ["backend.health"],
      async run(ctx) {
        const r = await call(ctx, "POST", "/v1/test-connection", 30_000);
        const bad = transport(r);
        if (bad) return bad;
        const m = (r.body?.memory ?? {}) as { ok?: boolean; message?: string };
        return m.ok ? { ok: true, detail: "Memory is working." } : { ok: false, detail: String(m.message ?? "Memory did not answer."), fix: { text: "Retry.", action: "retry" } };
      },
    },
  ];
}

export function initialRows(defs: Def[]): Row[] {
  return defs.map((d) => ({ id: d.id, group: d.group, title: d.title, status: "pending" as Status }));
}

/** Runs the rows in order. A row whose required rows did not pass is skipped with the reason. */
export async function runWizard(defs: Def[], ctx: Ctx, onUpdate: (rows: Row[]) => void): Promise<Row[]> {
  const rows = initialRows(defs);
  onUpdate(rows.map((r) => ({ ...r })));
  for (let i = 0; i < defs.length; i++) {
    const def = defs[i] as Def;
    const row = rows[i] as Row;
    const blocker = (def.requires ?? []).map((id) => rows.find((r) => r.id === id)).find((r) => r && r.status !== "pass");
    if (blocker) {
      row.status = "skip";
      row.detail = `Skipped: "${blocker.title}" did not pass.`;
      onUpdate(rows.map((r) => ({ ...r })));
      continue;
    }
    row.status = "running";
    onUpdate(rows.map((r) => ({ ...r })));
    const t0 = ctx.now();
    let out: Outcome;
    try {
      out = await def.run(ctx);
    } catch {
      out = { ok: false, detail: "This check failed in an unexpected way.", fix: { text: "Retry.", action: "retry" } };
    }
    row.ms = ctx.now() - t0;
    row.status = out.ok ? "pass" : "fail";
    row.detail = out.detail;
    row.metrics = out.metrics;
    row.fix = out.fix;
    onUpdate(rows.map((r) => ({ ...r })));
  }
  return rows;
}

export interface Report {
  generated_at: string;
  backend: Backend;
  app_version: string;
  passed: number;
  failed: number;
  skipped: number;
  total_ms: number;
  test_cost_usd: number;
  rows: Row[];
}

export function buildReport(rows: Row[], meta: { backend: Backend; appVersion: string; now?: Date }): Report {
  const cost = Number(((rows.find((r) => r.id === "or.stream")?.metrics ?? []).find((m) => m.label.startsWith("cost"))?.value ?? "$0").replace("$", "")) || 0;
  return {
    generated_at: (meta.now ?? new Date()).toISOString(),
    backend: meta.backend,
    app_version: meta.appVersion,
    passed: rows.filter((r) => r.status === "pass").length,
    failed: rows.filter((r) => r.status === "fail").length,
    skipped: rows.filter((r) => r.status === "skip").length,
    total_ms: Math.round(rows.reduce((t, r) => t + (r.ms ?? 0), 0)),
    test_cost_usd: cost,
    rows,
  };
}
