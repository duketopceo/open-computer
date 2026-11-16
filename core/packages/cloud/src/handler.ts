import { OPENROUTER_BASE_URL, accountRoute, toNdjson, verifyChain, type FetchLike } from "@open-computer/agent-core";
import defaults from "../../../contracts/defaults.json" with { type: "json" };
import { authenticate } from "./auth.ts";
import type { Env } from "./env.ts";
import { readSecret, SecretError } from "./models/secrets.ts";
import { D1MemoryStore } from "./storage/d1-memory.ts";
import { modelRefusal, settingRefusal } from "@open-computer/agent-core";
import { NO_POLICY, parsePolicy, PolicyError, resolveDailyCaps, validateUserDailyCap, type DailyCaps, type Policy } from "./policy.ts";
import { readAllAudit } from "./storage/ledger-client.ts";
import { handleDiag } from "./diag.ts";
import { cloudflareSandbox } from "./sandbox/cloudflare-port.ts";
import type { SandboxPort } from "./sandbox/port.ts";
import { WIZARD_HTML } from "./wizard/page.ts";

export const VERSION = "0.1.0";
const ID_RE = /^[A-Za-z0-9._-]{1,64}$/;
const KEY_FIELD = /key|secret|token|password|authorization|credential/i;

const json = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const problem = (status: number, code: string, message: string): Response => json({ error: { code, message } }, status);

function hasKeyField(value: unknown): boolean {
  if (Array.isArray(value)) return value.some(hasKeyField);
  if (value && typeof value === "object") {
    return Object.entries(value as Record<string, unknown>).some(([k, v]) => KEY_FIELD.test(k) || hasKeyField(v));
  }
  return false;
}

interface TaskBody {
  goal?: unknown;
  session_id?: unknown;
  model?: unknown;
  task_cap?: unknown;
  day_cap?: unknown;
  memory?: unknown;
}

/**
 * The cloud core's HTTP surface. `deps.fetch` is the only outbound network the handler itself uses (the model
 * key check); tests pass a fake through the test entry, production passes the global fetch.
 */
async function loadDailyCaps(env: Env, policy: Policy): Promise<DailyCaps> {
  const row = await env.DB.prepare("SELECT value FROM settings WHERE key = 'daily_cap_usd'").first<{ value: string }>();
  const stored = row ? Number(row.value) : null;
  return resolveDailyCaps(stored !== null && Number.isFinite(stored) ? stored : null, policy);
}

const MODEL_NAME = /^[A-Za-z0-9._:/-]{1,100}$/;

async function savedModels(env: Env): Promise<{ default_model: string; worker_model: string }> {
  const rows = await env.DB.prepare("SELECT key, value FROM settings WHERE key IN ('default_model', 'worker_model')").all<{ key: string; value: string }>();
  const get = (k: string): string | undefined => rows.results.find((r) => r.key === k)?.value;
  return { default_model: get("default_model") ?? defaults.default_model, worker_model: get("worker_model") ?? defaults.worker_model };
}

const settingsBody = (caps: DailyCaps, models: { default_model: string; worker_model: string }) => ({
  ...models,
  daily_cap_usd: caps.user,
  default_daily_cap_usd: defaults.daily_cap_usd,
  max_daily_cap_usd: defaults.max_daily_cap_usd,
  policy_max_daily_cap_usd: caps.policyMax,
  effective_daily_cap_usd: caps.effective,
});

/**
 * `fetch` is the only outbound network the handler itself uses (the model key check). `policy` supplies the IT
 * policy text; production leaves it unset, which reads the `POLICY_JSON` variable, and tests pass their own.
 */
export function createHandler(deps: {
  fetch: FetchLike;
  policy?: () => string | undefined;
  /** Sandbox for diagnostics; production uses the Container, tests pass a fake. */
  sandbox?: (env: Env, id: string) => SandboxPort | Promise<SandboxPort>;
}): ExportedHandler<Env> {
  return {
    async fetch(request, env) {
      // The setup page is static and carries no data or secret; it asks for the access token and calls the routes below.
      if (request.method === "GET") {
        const p = new URL(request.url).pathname;
        if (p === "/" || p === "/setup") {
          return new Response(WIZARD_HTML, {
            headers: {
              "content-type": "text/html; charset=utf-8",
              "cache-control": "no-store",
              "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'none'",
              "x-content-type-options": "nosniff",
              "referrer-policy": "no-referrer",
            },
          });
        }
      }
      const auth = await authenticate(request, env);
      if (auth === "blocked") return problem(429, "too_many_attempts", "Too many wrong tokens. Try again in a minute.");
      if (auth === "unconfigured") return problem(503, "not_configured", "The cloud core has no access secret yet.");
      if (auth !== "ok") return problem(401, "unauthorized", "Missing or wrong token.");

      const url = new URL(request.url);
      const path = url.pathname;
      const method = request.method;

      let policy: Policy = NO_POLICY;
      try {
        policy = parsePolicy(deps.policy ? deps.policy() : env.POLICY_JSON);
      } catch (e) {
        if (e instanceof PolicyError) return problem(500, "policy_invalid", `The IT policy cannot be read, so nothing runs until it is fixed: ${e.message}`);
        throw e;
      }

      if (path === "/v1/settings" && (method === "GET" || method === "PUT")) {
        if (method === "PUT") {
          let body: Record<string, unknown>;
          try {
            body = (await request.json()) as Record<string, unknown>;
          } catch {
            return problem(400, "bad_request", "The body must be JSON.");
          }
          const KNOWN = ["daily_cap_usd", "default_model", "worker_model"];
          for (const k of Object.keys(body)) {
            if (k === "custom_endpoint_url" || k === "custom_endpoint_model") return problem(400, "bad_request", "A custom endpoint needs its own key, which a cloud core cannot hold yet. Use a self-hosted or local core for it.");
            if (!KNOWN.includes(k)) return problem(400, "bad_request", `Unknown setting "${k}".`);
            const locked = settingRefusal(policy, k);
            if (locked) return problem(403, "policy_locked", locked);
          }
          if (Object.keys(body).length === 0) return problem(400, "bad_request", "Send at least one setting.");
          for (const k of ["default_model", "worker_model"] as const) {
            if (!(k in body)) continue;
            const v = body[k];
            if (v === null || v === "") await env.DB.prepare("DELETE FROM settings WHERE key = ?").bind(k).run();
            else if (typeof v === "string" && MODEL_NAME.test(v)) {
              const refused = k === "default_model" ? modelRefusal(policy, v) : null;
              if (refused) return problem(403, "policy_model", refused);
              await env.DB.prepare("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value").bind(k, v).run();
            } else return problem(400, "bad_request", `${k} must be a model name such as vendor/model.`);
          }
          if (!("daily_cap_usd" in body)) return json(settingsBody(await loadDailyCaps(env, policy), await savedModels(env)));
          if (body.daily_cap_usd === null) {
            await env.DB.prepare("DELETE FROM settings WHERE key = 'daily_cap_usd'").run();
          } else {
            const checked = validateUserDailyCap(body.daily_cap_usd);
            if (typeof checked === "string") return problem(400, "bad_request", checked);
            await env.DB.prepare("INSERT INTO settings (key, value) VALUES ('daily_cap_usd', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value")
              .bind(String(checked))
              .run();
          }
        }
        return json(settingsBody(await loadDailyCaps(env, policy), await savedModels(env)));
      }

      if (method === "GET" && path === "/v1/health") return json({ ok: true, version: VERSION });

      if (path.startsWith("/v1/diag/")) {
        const caps = await loadDailyCaps(env, policy);
        const diag = await handleDiag(
          request,
          env,
          { fetch: deps.fetch, sandbox: async (id) => (deps.sandbox ? deps.sandbox(env, id) : cloudflareSandbox(env, id)), dayCap: caps.effective },
          VERSION,
        );
        if (diag) return diag;
      }

      if (method === "GET" && path === "/v1/model/health") return modelHealth(env, deps.fetch);

      // Account data for the desktop and console: the official OpenRouter SDK behind this core's own authenticated routes.
      const orRoute = /^\/v1\/openrouter\/(key|credits|models|generations)$/.exec(path);
      if (method === "GET" && orRoute) {
        let apiKey: string;
        try {
          apiKey = await readSecret(env.MODEL_KEY, "model_key");
        } catch (e) {
          if (e instanceof SecretError) return problem(409, "key_missing", "No OpenRouter key is stored on this core yet.");
          throw e;
        }
        const reply = await accountRoute(orRoute[1] as string, { apiKey, fetch: deps.fetch, audit: () => readAllAudit(env) });
        return reply ? json(reply.body, reply.status) : problem(404, "not_found", "No such endpoint.");
      }

      if (method === "POST" && path === "/v1/memory/probe") {
        try {
          const store = new D1MemoryStore(env.DB);
          const id = `probe:${crypto.randomUUID()}`;
          await store.addHistory({ id, taskId: "probe", title: "probe", summary: "probe note", ts: new Date().toISOString() });
          const back = (await store.listHistory(50)).some((h) => h.id === id);
          await store.deleteHistory(id);
          return json({ ok: back }, back ? 200 : 500);
        } catch {
          return json({ ok: false }, 500);
        }
      }

      if (method === "POST" && path === "/v1/tasks") {
        let body: TaskBody;
        try {
          body = (await request.json()) as TaskBody;
        } catch {
          return problem(400, "bad_request", "The body must be JSON.");
        }
        if (hasKeyField(body)) {
          return problem(400, "keys_not_accepted", "Keys are never accepted in a task. Store them in the cloud core's secret store.");
        }
        if (typeof body.goal !== "string" || body.goal.trim() === "" || body.goal.length > 8000) {
          return problem(400, "bad_request", "goal must be text of 1 to 8000 characters.");
        }
        const sessionId = body.session_id === undefined ? crypto.randomUUID() : String(body.session_id);
        if (!ID_RE.test(sessionId)) return problem(400, "bad_request", "session_id may use letters, digits, dot, dash and underscore.");
        const taskCap = body.task_cap === undefined ? 1 : Number(body.task_cap);
        if (!Number.isFinite(taskCap) || taskCap <= 0 || taskCap > 100) return problem(400, "bad_request", "task_cap must be above 0 and at most 100.");
        // The daily cap is not optional. The user's cap (else the default), held under any IT policy maximum, always
        // applies; a task can ask for a lower one but never a higher one.
        const caps = await loadDailyCaps(env, policy);
        const askedDayCap = body.day_cap === undefined || body.day_cap === null ? caps.effective : Number(body.day_cap);
        if (!Number.isFinite(askedDayCap) || askedDayCap <= 0) return problem(400, "bad_request", "day_cap must be above 0.");
        const dayCap = Math.min(askedDayCap, caps.effective);
        const model = typeof body.model === "string" && body.model !== "" ? body.model : (await savedModels(env)).default_model;
        const refused = modelRefusal(policy, model);
        if (refused) return problem(403, "policy_model", refused);
        const taskId = crypto.randomUUID();
        const session = env.SESSION.get(env.SESSION.idFromName(sessionId));
        const started = await session.startTask({
          taskId,
          sessionId,
          goal: body.goal,
          model,
          taskCap,
          dayCap,
          memoryEnabled: body.memory !== false,
        });
        if ("busy" in started) {
          return problem(409, "session_busy", "This session already has a task running. Wait for it to finish or start a new session.");
        }
        try {
          await env.TASK_WORKFLOW.create({ id: taskId, params: { sessionId, taskId } });
        } catch {
          await session.failTask(taskId, "The task could not be started.");
          return problem(503, "workflow_unavailable", "The task could not be started. Try again shortly.");
        }
        return json({ task_id: taskId, session_id: sessionId, day_cap: dayCap }, 202);
      }

      const sessionMatch = /^\/v1\/sessions\/([^/]+)\/(.*)$/.exec(path);
      if (sessionMatch) {
        const sessionId = sessionMatch[1] as string;
        const rest = sessionMatch[2] as string;
        if (!ID_RE.test(sessionId)) return problem(400, "bad_request", "Bad session id.");
        const session = env.SESSION.get(env.SESSION.idFromName(sessionId));
        if (method === "GET" && rest === "events") {
          const headers = new Headers();
          const last = request.headers.get("last-event-id");
          if (last) headers.set("last-event-id", last);
          return session.fetch(new Request("https://session/events", { headers }));
        }
        const approval = /^approvals\/([^/]+)$/.exec(rest);
        if (method === "POST" && approval) {
          let body: { hash?: unknown; approved?: unknown };
          try {
            body = (await request.json()) as typeof body;
          } catch {
            return problem(400, "bad_request", "The body must be JSON.");
          }
          if (typeof body.hash !== "string" || typeof body.approved !== "boolean") {
            return problem(400, "bad_request", "hash and approved are required.");
          }
          const result = await session.decideApproval(approval[1] as string, { hash: body.hash, approved: body.approved });
          if (result.ok) return json({ ok: true, outcome: result.outcome });
          const status = result.reason === "unknown" ? 404 : result.reason === "workflow_unavailable" ? 503 : 409;
          return problem(status, result.reason, `The approval was not recorded: ${result.reason.replace("_", " ")}.`);
        }
        const cancel = /^tasks\/([^/]+)\/cancel$/.exec(rest);
        if (method === "POST" && cancel) {
          await session.cancelTask(cancel[1] as string);
          return json({ ok: true });
        }
      }

      if (method === "GET" && path === "/v1/audit/export") {
        const records = await readAllAudit(env);
        const chain = await verifyChain(records);
        return new Response(toNdjson(records), {
          headers: { "content-type": "application/x-ndjson; charset=utf-8", "x-audit-chain": chain.ok ? "ok" : "broken" },
        });
      }

      return problem(404, "not_found", "No such endpoint.");
    },
  };
}

async function modelHealth(env: Env, fetchImpl: FetchLike): Promise<Response> {
  let key: string;
  try {
    key = await readSecret(env.MODEL_KEY, "model_key");
  } catch (e) {
    if (e instanceof SecretError) return json({ ok: false, code: "key_missing" });
    throw e;
  }
  try {
    // Key-info request: proves the key is valid without spending anything.
    const res = await fetchImpl(`${OPENROUTER_BASE_URL}/key`, {
      method: "GET",
      headers: { authorization: `Bearer ${key}` },
      signal: AbortSignal.timeout(4000),
    });
    if (res.status >= 200 && res.status < 300) return json({ ok: true });
    if (res.status === 401 || res.status === 403) return json({ ok: false, code: "key_invalid" });
    if (res.status === 402) return json({ ok: false, code: "payment_required" });
    if (res.status === 429) return json({ ok: false, code: "rate_limited" });
    return json({ ok: false, code: "error" });
  } catch {
    return json({ ok: false, code: "error" });
  }
}
