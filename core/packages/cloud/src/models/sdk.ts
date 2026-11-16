import type { FetchLike } from "@open-computer/agent-core";
import { OpenRouter } from "@openrouter/sdk";
import { HTTPClient } from "@openrouter/sdk/lib/http.js";

/**
 * OpenRouter's official TypeScript SDK, used for the control calls (key info, model list) where typed responses and
 * its error classes help. The chat stream stays on the raw client in agent-core, on purpose: the SDK re-validates
 * every streamed object against its schema and drops fields it does not know. Reasoning blocks must be replayed to
 * the provider exactly as received, and a probe against the current SDK showed an unknown field inside a
 * `reasoning_details` item was removed. Streaming also needs the three-stage timeout guard and the recorded fixtures
 * that the raw client owns.
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
  // Bundlers can rename classes; an SDK error that carries a success status can only be a body it could not parse.
  const status = sdkStatus(e);
  return status !== null && status >= 200 && status < 300;
}
