import { OPENROUTER_BASE_URL, toNdjson, verifyChain, type FetchLike } from "@open-computer/agent-core";
import { authenticate } from "./auth.ts";
import type { Env } from "./env.ts";
import { readSecret, SecretError } from "./models/secrets.ts";
import { D1MemoryStore } from "./storage/d1-memory.ts";
import { readAllAudit } from "./storage/ledger-client.ts";

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
export function createHandler(deps: { fetch: FetchLike }): ExportedHandler<Env> {
  return {
    async fetch(request, env) {
      const auth = await authenticate(request, env);
      if (auth === "blocked") return problem(429, "too_many_attempts", "Too many wrong tokens. Try again in a minute.");
      if (auth === "unconfigured") return problem(503, "not_configured", "The cloud core has no access secret yet.");
      if (auth !== "ok") return problem(401, "unauthorized", "Missing or wrong token.");

      const url = new URL(request.url);
      const path = url.pathname;
      const method = request.method;

      if (method === "GET" && path === "/v1/health") return json({ ok: true, version: VERSION });

      if (method === "GET" && path === "/v1/model/health") return modelHealth(env, deps.fetch);

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
        const dayCap = body.day_cap === undefined || body.day_cap === null ? null : Number(body.day_cap);
        if (dayCap !== null && (!Number.isFinite(dayCap) || dayCap <= 0)) return problem(400, "bad_request", "day_cap must be above 0.");
        const model = typeof body.model === "string" && body.model !== "" ? body.model : env.DEFAULT_MODEL;
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
        return json({ task_id: taskId, session_id: sessionId }, 202);
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
