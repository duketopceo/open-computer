import {
  DEFAULT_PROVIDER_TIMEOUTS,
  OPENROUTER_BASE_URL,
  ProviderError,
  openRouterProvider,
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
 * The preferred path: the Workers AI binding knows the account, so the gateway address is read from it. Falls back to the
 * account-id route, then to calling OpenRouter directly, so a gateway problem never stops the core from answering.
 */
export async function resolveTarget(env: GatewayEnv): Promise<ProviderTarget> {
  if (env.AI && env.AI_GATEWAY_ID) {
    try {
      const url = await env.AI.gateway(env.AI_GATEWAY_ID).getUrl("openrouter");
      if (url) return { baseUrl: url.replace(/\/+$/, ""), viaGateway: true, source: "binding" };
    } catch {
      // fall through
    }
  }
  return providerTarget(env);
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
    fetch: fetchImpl,
    baseUrl: async () => (await resolveTarget(env)).baseUrl,
    timeouts: modelTimeouts(env),
    // An AI Gateway with authentication on needs the Cloudflare token on every call. It comes from the secret store, never from a request.
    headers: async (): Promise<Record<string, string>> => {
      if (!gatewayConfigured(env) || !env.AIG_TOKEN) return {};
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
