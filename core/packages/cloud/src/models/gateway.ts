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
}

/**
 * With a gateway account and id configured, calls go through Cloudflare AI Gateway's OpenRouter route
 * (logs, budgets, fallback); otherwise straight to OpenRouter. The gateway is optional.
 * The route shape follows Cloudflare's published AI Gateway pattern and has not been exercised against a live gateway.
 */
export function providerTarget(env: Pick<Env, "AI_GATEWAY_ACCOUNT" | "AI_GATEWAY_ID">): ProviderTarget {
  if (env.AI_GATEWAY_ACCOUNT && env.AI_GATEWAY_ID) {
    return {
      baseUrl: `https://gateway.ai.cloudflare.com/v1/${env.AI_GATEWAY_ACCOUNT}/${env.AI_GATEWAY_ID}/openrouter`,
      viaGateway: true,
    };
  }
  return { baseUrl: OPENROUTER_BASE_URL, viaGateway: false };
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
  const target = providerTarget(env);
  return openRouterProvider({
    fetch: fetchImpl,
    baseUrl: target.baseUrl,
    timeouts: modelTimeouts(env),
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
