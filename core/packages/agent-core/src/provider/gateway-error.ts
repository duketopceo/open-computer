/**
 * Cloudflare AI Gateway reports its own failures in a shape of its own: `{ "success": false, "error": [{ "code": 2009,
 * "message": "Unauthorized" }] }`. OpenRouter's errors look different (`{ "error": { "code": 401, ... } }`), so the two
 * can be told apart by the body alone. Code 2009 means the gateway needs authentication and the request carried none.
 */
export interface GatewayErrorBody {
  code: number;
  message: string;
}

export function gatewayErrorFromBody(text: string): GatewayErrorBody | null {
  try {
    const j = JSON.parse(text) as { success?: unknown; error?: unknown };
    if (j && j.success === false && Array.isArray(j.error)) {
      const first = j.error[0] as { code?: unknown; message?: unknown } | undefined;
      if (first && typeof first.code === "number") return { code: first.code, message: String(first.message ?? "") };
    }
  } catch {
    // not JSON: not a gateway error body
  }
  return null;
}

export const GATEWAY_NEEDS_AUTH = "AI Gateway refused the request (needs authentication).";
