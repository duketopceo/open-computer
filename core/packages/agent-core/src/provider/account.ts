import { OpenRouter } from "@openrouter/sdk";
import { HTTPClient } from "@openrouter/sdk/lib/http.js";
import { OPENROUTER_BASE_URL } from "./openrouter.ts";
import type { FetchLike } from "./types.ts";
import { recentGenerations } from "../audit/generations.ts";
import type { AuditRecord } from "../audit/log.ts";

/**
 * OpenRouter's official TypeScript SDK, used for the control calls: key info, credits, the model list, generation
 * lookup and the OAuth PKCE code exchange. The chat stream stays on the raw client in `openrouter.ts` on purpose: the
 * SDK re-validates every streamed object against its schema and drops fields it does not know, and reasoning blocks must
 * be replayed to the provider exactly as received.
 */
export function openRouterSdk(apiKey: string, fetchImpl: FetchLike, serverURL?: string): OpenRouter {
  const httpClient = new HTTPClient({
    // The SDK passes a Request object with no init, so method, headers and body must be carried over explicitly.
    fetcher: async (input, init) => {
      if (typeof input === "string" || input instanceof URL) return fetchImpl(input.toString(), init ?? {});
      const req = input as Request;
      const hasBody = req.method !== "GET" && req.method !== "HEAD";
      return fetchImpl(req.url, {
        method: req.method,
        headers: req.headers,
        ...(hasBody ? { body: await req.clone().arrayBuffer() } : {}),
        signal: req.signal,
        ...init,
      });
    },
  });
  return new OpenRouter({ apiKey, httpClient, ...(serverURL ? { serverURL } : {}) });
}

/** HTTP status carried by an SDK error, or null for a transport failure. */
export function sdkStatus(e: unknown): number | null {
  const s = (e as { statusCode?: unknown })?.statusCode;
  return typeof s === "number" ? s : null;
}

/** True when the SDK rejected the response body against its schema (as opposed to an HTTP or transport error). */
export function isValidationError(e: unknown): boolean {
  const name = (e as { name?: unknown })?.name;
  if (typeof name === "string" && /validation/i.test(name)) return true;
  const status = sdkStatus(e);
  return status !== null && status >= 200 && status < 300;
}

export interface AccountContext {
  apiKey: string;
  fetch: FetchLike;
  /** Override for tests and custom hosts; the SDK default is OpenRouter. */
  serverURL?: string;
  /** Audit records, for the recent generation ids. */
  audit?: () => Promise<AuditRecord[]>;
}

export interface AccountReply {
  status: number;
  body: Record<string, unknown>;
}

const fail = (e: unknown): AccountReply => {
  const s = sdkStatus(e);
  // Only 401 means the key is bad. A 403 is a permission the key lacks (credits need a management key), not a rejected key.
  if (s === 401) return { status: 401, body: { error: { code: "key_invalid", message: "OpenRouter rejected the key." } } };
  if (s === 403) return { status: 403, body: { error: { code: "forbidden", message: "OpenRouter does not allow this key to read that." } } };
  if (s === 402) return { status: 402, body: { error: { code: "payment_required", message: "The key has no credit or limit left." } } };
  if (s === 429) return { status: 429, body: { error: { code: "rate_limited", message: "OpenRouter is rate limiting this key." } } };
  return { status: 502, body: { error: { code: s === null ? "unreachable" : "openrouter_error", message: "OpenRouter could not answer." } } };
};

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const perMillion = (v: unknown): number => Number(v ?? 0) * 1_000_000 || 0;

/** The plain HTTP read, used only when the SDK's strict response schema rejects a body the API really sent. */
async function raw(c: AccountContext, path: string): Promise<Record<string, unknown>> {
  const res = await c.fetch(`${(c.serverURL ?? OPENROUTER_BASE_URL).replace(/\/$/, "")}${path}`, {
    method: "GET",
    headers: { authorization: `Bearer ${c.apiKey}` },
    signal: AbortSignal.timeout(15000),
  });
  if (!res.ok) throw Object.assign(new Error("http"), { statusCode: res.status });
  return (await res.json()) as Record<string, unknown>;
}
const rec = (v: unknown): Record<string, unknown> => (v && typeof v === "object" ? (v as Record<string, unknown>) : {});

const keyBody = (d: Record<string, unknown>) => ({
  label: String(d.label ?? ""),
  usage: num(d.usage) ?? 0,
  limit: num(d.limit),
  limit_remaining: num(d.limit_remaining ?? d.limitRemaining),
  is_free_tier: (d.is_free_tier ?? d.isFreeTier) === true,
  /** How often the limit resets: `daily`, `weekly`, `monthly`, or null when it never does. */
  limit_reset: typeof (d.limit_reset ?? d.limitReset) === "string" ? String(d.limit_reset ?? d.limitReset) : null,
  usage_daily: num(d.usage_daily ?? d.usageDaily),
  usage_weekly: num(d.usage_weekly ?? d.usageWeekly),
  usage_monthly: num(d.usage_monthly ?? d.usageMonthly),
});

/** `GET /v1/openrouter/key`: usage and limit of the stored key. The key itself is never returned. */
export async function accountKey(c: AccountContext): Promise<AccountReply> {
  try {
    const d = (await openRouterSdk(c.apiKey, c.fetch, c.serverURL).apiKeys.getCurrentKeyMetadata()).data;
    return { status: 200, body: keyBody(d as unknown as Record<string, unknown>) };
  } catch (e) {
    if (!isValidationError(e)) return fail(e);
    try {
      return { status: 200, body: keyBody(rec((await raw(c, "/key")).data)) };
    } catch (e2) {
      return fail(e2);
    }
  }
}

/** `GET /v1/openrouter/credits`. */
export async function accountCredits(c: AccountContext): Promise<AccountReply> {
  // An ordinary key (what the OAuth sign-in makes) cannot read account credits: OpenRouter answers 403. That is not a
  // rejected key, so say plainly that credits need a management key and let the key's own usage and limit speak.
  const needsManagement: AccountReply = { status: 200, body: { available: false, reason: "management_key_required" } };
  try {
    const d = (await openRouterSdk(c.apiKey, c.fetch, c.serverURL).credits.getCredits()).data;
    return { status: 200, body: { available: true, total: num(d.totalCredits) ?? 0, used: num(d.totalUsage) ?? 0 } };
  } catch (e) {
    if (sdkStatus(e) === 403) return needsManagement;
    if (!isValidationError(e)) return fail(e);
    try {
      const d = rec((await raw(c, "/credits")).data);
      return { status: 200, body: { available: true, total: num(d.total_credits) ?? 0, used: num(d.total_usage) ?? 0 } };
    } catch (e2) {
      return sdkStatus(e2) === 403 ? needsManagement : fail(e2);
    }
  }
}

/** `GET /v1/openrouter/models`: every model with its price in dollars per million tokens. */
export async function accountModels(c: AccountContext): Promise<AccountReply> {
  try {
    const sdk = openRouterSdk(c.apiKey, c.fetch, c.serverURL);
    const models: Array<Record<string, unknown>> = [];
    for await (const page of await sdk.models.list()) {
      for (const m of page.result.data) {
        const params = ((m.supportedParameters ?? []) as string[]).map(String);
        models.push({
          id: m.id,
          name: m.name,
          context: m.contextLength ?? 0,
          prompt_per_m: perMillion(m.pricing?.prompt),
          completion_per_m: perMillion(m.pricing?.completion),
          tools: params.includes("tools"),
          reasoning: params.includes("reasoning") || params.includes("include_reasoning"),
        });
      }
    }
    return { status: 200, body: { models } };
  } catch (e) {
    if (!isValidationError(e)) return fail(e);
    try {
      const list = (rec(await raw(c, "/models")).data as unknown[]) ?? [];
      const models = list.map((m) => {
        const o = rec(m);
        const pr = rec(o.pricing);
        const params = Array.isArray(o.supported_parameters) ? (o.supported_parameters as unknown[]).map(String) : [];
        return {
          id: String(o.id ?? ""),
          name: String(o.name ?? ""),
          context: num(o.context_length) ?? 0,
          prompt_per_m: perMillion(pr.prompt),
          completion_per_m: perMillion(pr.completion),
          tools: params.includes("tools"),
          reasoning: params.includes("reasoning") || params.includes("include_reasoning"),
        };
      });
      return { status: 200, body: { models } };
    } catch (e2) {
      return fail(e2);
    }
  }
}

/** `GET /v1/openrouter/generations`: the recent model calls the core made, each looked up for cost, tokens and latency. */
export async function accountGenerations(c: AccountContext, limit = 5): Promise<AccountReply> {
  const recent = c.audit ? recentGenerations(await c.audit(), 5, limit) : [];
  const sdk = openRouterSdk(c.apiKey, c.fetch, c.serverURL);
  const generations: Array<Record<string, unknown>> = [];
  for (const g of recent) {
    try {
      const d = (await sdk.generations.getGeneration({ id: g.id })).data;
      generations.push({
        id: g.id,
        task_id: g.task_id,
        ts: g.ts,
        model: d.model,
        cost: num(d.totalCost) ?? 0,
        prompt_tokens: num(d.tokensPrompt) ?? 0,
        completion_tokens: num(d.tokensCompletion) ?? 0,
        latency_ms: num(d.latency),
      });
    } catch (e) {
      if (isValidationError(e)) {
        try {
          const d = rec((await raw(c, `/generation?id=${encodeURIComponent(g.id)}`)).data);
          generations.push({
            id: g.id,
            task_id: g.task_id,
            ts: g.ts,
            model: String(d.model ?? ""),
            cost: num(d.total_cost) ?? 0,
            prompt_tokens: num(d.tokens_prompt) ?? 0,
            completion_tokens: num(d.tokens_completion) ?? 0,
            latency_ms: num(d.latency),
          });
          continue;
        } catch {
          // fall through to the pending entry
        }
      }
      // The record can lag a few seconds behind the call; the id is still shown.
      generations.push({ id: g.id, task_id: g.task_id, ts: g.ts, model: "", cost: 0, prompt_tokens: 0, completion_tokens: 0, latency_ms: null, pending: true });
    }
  }
  return { status: 200, body: { generations } };
}

/** The OAuth PKCE code exchange through the SDK. Returns the new key, or null. */
export async function exchangeAuthCode(c: Omit<AccountContext, "apiKey">, code: string, verifier: string): Promise<string | null> {
  try {
    const res = await openRouterSdk("", c.fetch, c.serverURL).oAuth.exchangeAuthCodeForAPIKey({
      requestBody: { code, codeVerifier: verifier, codeChallengeMethod: "S256" },
    });
    return res.key || null;
  } catch {
    return null;
  }
}

export async function accountRoute(name: string, c: AccountContext): Promise<AccountReply | null> {
  switch (name) {
    case "key":
      return accountKey(c);
    case "credits":
      return accountCredits(c);
    case "models":
      return accountModels(c);
    case "generations":
      return accountGenerations(c);
    default:
      return null;
  }
}
