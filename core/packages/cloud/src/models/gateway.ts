import {
  DEFAULT_PROVIDER_TIMEOUTS,
  OPENROUTER_BASE_URL,
  ProviderError,
  openRouterProvider,
  GATEWAY_NEEDS_AUTH,
  gatewayErrorFromBody,
  type FetchLike,
  type Provider,
} from "@open-computer/agent-core";
import type { Env } from "../env.ts";
import { readSecret, SecretError } from "./secrets.ts";

export interface ProviderTarget {
  baseUrl: string;
  viaGateway: boolean;
  /** Where the gateway address came from: the Workers AI binding, the deploy-time account id, or nowhere (direct). */
  source?: "binding" | "account" | "direct";
}

type GatewayEnv = Pick<Env, "AI_GATEWAY_ACCOUNT" | "AI_GATEWAY_ID"> & { AI?: Env["AI"] };

/**
 * With a gateway account and id configured, calls go through Cloudflare AI Gateway's OpenRouter route
 * (logs, budgets, fallback); otherwise straight to OpenRouter. The gateway is optional.
 * The route shape follows Cloudflare's documented pattern (`.../{account}/{gateway}/openrouter` + `/chat/completions`).
 */
export function providerTarget(env: Pick<Env, "AI_GATEWAY_ACCOUNT" | "AI_GATEWAY_ID">): ProviderTarget {
  if (env.AI_GATEWAY_ACCOUNT && env.AI_GATEWAY_ID) {
    return {
      baseUrl: `https://gateway.ai.cloudflare.com/v1/${env.AI_GATEWAY_ACCOUNT}/${env.AI_GATEWAY_ID}/openrouter`,
      viaGateway: true,
      source: "account",
    };
  }
  return { baseUrl: OPENROUTER_BASE_URL, viaGateway: false, source: "direct" };
}

/** Whether the core is set up to use a gateway at all (binding plus id, or account plus id). */
export function gatewayConfigured(env: GatewayEnv): boolean {
  return !!env.AI_GATEWAY_ID && (!!env.AI || !!env.AI_GATEWAY_ACCOUNT);
}

/**
 * The pseudo-address of the binding path. It is never fetched: `gatewayFetch` recognises it and sends the request through
 * `env.AI.gateway(id).run(...)`, which authenticates as the Worker, so an Authenticated Gateway needs no token here.
 * (A plain fetch to the gateway URL from `getUrl()` carries no credential and gets 401 / code 2009 from such a gateway.)
 */
export const BINDING_BASE = "binding://ai-gateway/openrouter";
const GATEWAY_HOST = "https://gateway.ai.cloudflare.com/";

/** The preferred path is the binding; then the account-id route; then OpenRouter directly. */
export async function resolveTarget(env: GatewayEnv): Promise<ProviderTarget> {
  if (env.AI && env.AI_GATEWAY_ID) return { baseUrl: BINDING_BASE, viaGateway: true, source: "binding" };
  return providerTarget(env);
}

// ---------- what the last gateway attempt did, shared by every isolate through D1 ----------

export interface GatewayState {
  ok: boolean;
  reason?: string;
  at: string;
}

const STATE_KEY = "gateway_last";
const memo = new WeakMap<object, string>();

/** Records the outcome of a gateway attempt. Writes only when the outcome changed, so a healthy gateway costs nothing. */
export async function recordGateway(env: Pick<Env, "DB">, ok: boolean, reason?: string): Promise<void> {
  const sig = ok ? "ok" : `fail:${reason ?? ""}`;
  if (memo.get(env.DB) === sig) return;
  memo.set(env.DB, sig);
  try {
    const state: GatewayState = { ok, ...(reason ? { reason } : {}), at: new Date().toISOString() };
    await env.DB.prepare("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value").bind(STATE_KEY, JSON.stringify(state)).run();
  } catch {
    // Reporting must never break a model call.
  }
}

export async function readGatewayState(env: Pick<Env, "DB">): Promise<GatewayState | null> {
  try {
    const row = await env.DB.prepare("SELECT value FROM settings WHERE key = ?").bind(STATE_KEY).first<{ value: string }>();
    return row ? (JSON.parse(row.value) as GatewayState) : null;
  } catch {
    return null;
  }
}

/** What `/v1/diag/bindings` says about the gateway, given the target and the last recorded attempt. */
export function gatewayReport(env: GatewayEnv, target: ProviderTarget, last: GatewayState | null) {
  const refused = target.viaGateway && last?.ok === false;
  const effective = target.viaGateway && !refused;
  return {
    gateway: effective,
    gateway_configured: gatewayConfigured(env),
    gateway_via: effective ? (target.source ?? "direct") : "direct",
    ...(refused ? { gateway_reason: last?.reason ?? "AI Gateway refused the request.", gateway_checked_at: last?.at } : {}),
  };
}

// ---------- the request path ----------

interface GatewayFailure {
  reason: string;
}

/** A thrown AiGatewayError (httpCode / internalCode) from the binding, or any other thrown failure of the call. */
function describeThrown(e: unknown): GatewayFailure {
  const x = e as { httpCode?: unknown; internalCode?: unknown; message?: unknown };
  const http = typeof x?.httpCode === "number" ? x.httpCode : null;
  const code = typeof x?.internalCode === "number" ? x.internalCode : null;
  if (http === 401 || code === 2009) return { reason: GATEWAY_NEEDS_AUTH };
  if (http !== null || code !== null) return { reason: `AI Gateway error (HTTP ${http ?? "?"}, code ${code ?? "?"}).` };
  return { reason: "AI Gateway could not be reached." };
}

async function describeResponse(res: Response): Promise<GatewayFailure | null> {
  if (res.ok) return null;
  const body = gatewayErrorFromBody(await res.clone().text().catch(() => ""));
  if (!body) return null; // OpenRouter's own error (401, 402, 429 ...): pass it through, it is not the gateway's
  return body.code === 2009 || res.status === 401 ? { reason: GATEWAY_NEEDS_AUTH } : { reason: `AI Gateway error (HTTP ${res.status}, code ${body.code}).` };
}

const toRecord = (h: HeadersInit | undefined): Record<string, string> => {
  const out: Record<string, string> = {};
  if (!h) return out;
  new Headers(h).forEach((v, k) => (out[k] = v));
  return out;
};

/**
 * Model calls to the gateway, with one retry straight to OpenRouter when the gateway itself refuses or fails. Only
 * gateway-origin failures retry: OpenRouter's own 401, 402 and 429 pass through untouched. Streaming is untouched too,
 * because the gateway's Response body is handed on as it is.
 */
export function gatewayFetch(env: Pick<Env, "AI" | "AI_GATEWAY_ID" | "DB">, direct: FetchLike): FetchLike {
  return async (url, init) => {
    const viaBinding = url.startsWith(BINDING_BASE);
    const viaUrl = !viaBinding && url.startsWith(GATEWAY_HOST);
    if (!viaBinding && !viaUrl) return direct(url, init);

    const path = viaBinding ? url.slice(BINDING_BASE.length) : url.slice(url.indexOf("/openrouter") + "/openrouter".length);
    const directUrl = `${OPENROUTER_BASE_URL}${path}`;
    const fallBack = async (f: GatewayFailure): Promise<Response> => {
      await recordGateway(env, false, f.reason);
      return direct(directUrl, init);
    };

    let res: Response;
    try {
      if (viaBinding) {
        const headers = toRecord(init.headers);
        delete headers["cf-aig-authorization"]; // the binding authenticates as the Worker
        let query: unknown = {};
        try {
          query = JSON.parse(String(init.body ?? "{}"));
        } catch {
          query = {};
        }
        res = await (env.AI as Ai).gateway(env.AI_GATEWAY_ID as string).run(
          { provider: "openrouter", endpoint: path.replace(/^\//, ""), headers, query },
          init.signal ? { signal: init.signal } : undefined,
        );
      } else {
        res = await direct(url, init);
      }
    } catch (e) {
      if ((e as { name?: unknown })?.name === "AbortError" || init.signal?.aborted) throw e;
      return fallBack(describeThrown(e));
    }
    const failure = await describeResponse(res);
    if (failure) return fallBack(failure);
    await recordGateway(env, true);
    return res;
  };
}

/**
 * Builds the model provider for the cloud core. The key comes only from the Secrets Store binding; a key
 * in a request or task body is never consulted.
 */
function ms(value: string | undefined, fallback: number): number {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? n : fallback;
}

export function modelTimeouts(env: Pick<Env, "MODEL_CONNECT_TIMEOUT_MS" | "MODEL_IDLE_TIMEOUT_MS" | "MODEL_TOTAL_TIMEOUT_MS">) {
  return {
    connectMs: ms(env.MODEL_CONNECT_TIMEOUT_MS, DEFAULT_PROVIDER_TIMEOUTS.connectMs),
    idleMs: ms(env.MODEL_IDLE_TIMEOUT_MS, DEFAULT_PROVIDER_TIMEOUTS.idleMs),
    totalMs: ms(env.MODEL_TOTAL_TIMEOUT_MS, DEFAULT_PROVIDER_TIMEOUTS.totalMs),
  };
}

export function buildProvider(env: Env, fetchImpl: FetchLike): Provider {
  return openRouterProvider({
    fetch: gatewayFetch(env, fetchImpl),
    baseUrl: async () => (await resolveTarget(env)).baseUrl,
    timeouts: modelTimeouts(env),
    // An AI Gateway with authentication on needs the Cloudflare token on every call. It comes from the secret store, never from a request.
    headers: async (): Promise<Record<string, string>> => {
      // The binding authenticates as the Worker; the token is only for the plain-URL (account id) route.
      if (env.AI || !gatewayConfigured(env) || !env.AIG_TOKEN) return {};
      try {
        return { "cf-aig-authorization": `Bearer ${await readSecret(env.AIG_TOKEN, "aig_token")}` };
      } catch {
        return {};
      }
    },
    apiKey: async () => {
      try {
        return await readSecret(env.MODEL_KEY, "model_key");
      } catch (e) {
        if (e instanceof SecretError) throw new ProviderError("key_missing", "No model key is stored in the cloud core yet.");
        throw e;
      }
    },
  });
}
