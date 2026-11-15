import { OPENROUTER_BASE_URL, openRouterProvider, type FetchLike, type Provider } from "@open-computer/agent-core";
import type { Env } from "../env.ts";
import { readSecret } from "./secrets.ts";

export interface ProviderTarget {
  baseUrl: string;
  viaGateway: boolean;
}

/**
 * With a gateway account and id configured, calls go through Cloudflare AI Gateway's OpenRouter route
 * (logs, budgets, fallback); otherwise straight to OpenRouter. The gateway is optional.
 * The route shape follows Cloudflare's published AI Gateway pattern; U16's first real run confirms it.
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
export function buildProvider(env: Env, fetchImpl: FetchLike): Provider {
  const target = providerTarget(env);
  return openRouterProvider({
    fetch: fetchImpl,
    baseUrl: target.baseUrl,
    apiKey: () => readSecret(env.MODEL_KEY, "model_key"),
  });
}
