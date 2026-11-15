/**
 * The handler that sits outside the sandbox on its outbound traffic. It decides which hosts a sandbox can
 * reach, strips any credential the sandbox process set itself, and adds the configured credential for that
 * host. Model-provider hosts can never be credentialed: model calls and their spend stay in the Workflow.
 */

export interface OutboundRule {
  host: string;
  header: string;
  /** Resolves the credential value at request time (Secrets Store); never logged. */
  secret: () => Promise<string>;
  scheme?: "Bearer" | "";
}

export interface OutboundAudit {
  (kind: "outbound.denied" | "outbound.credentialed", data: Record<string, unknown>): void | Promise<void>;
}

export const PROVIDER_HOSTS = [
  "openrouter.ai",
  "api.anthropic.com",
  "api.openai.com",
  "generativelanguage.googleapis.com",
  "api.groq.com",
  "gateway.ai.cloudflare.com",
];

const STRIPPED_HEADERS = ["authorization", "proxy-authorization", "x-api-key", "cookie"];

export class OutboundConfigError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "OutboundConfigError";
  }
}

export function createOutboundHandler(options: {
  rules: OutboundRule[];
  upstream: (request: Request) => Promise<Response>;
  audit: OutboundAudit;
}): (request: Request) => Promise<Response> {
  for (const rule of options.rules) {
    const host = rule.host.toLowerCase();
    if (PROVIDER_HOSTS.some((p) => host === p || host.endsWith(`.${p}`))) {
      throw new OutboundConfigError(`"${rule.host}" is a model-provider host and cannot be credentialed from a sandbox.`);
    }
  }
  const byHost = new Map(options.rules.map((r) => [r.host.toLowerCase(), r]));

  return async (request) => {
    const url = new URL(request.url);
    const rule = byHost.get(url.hostname.toLowerCase());
    if (!rule) {
      await options.audit("outbound.denied", { host: url.hostname, method: request.method });
      return new Response("Blocked: this host is not on the sandbox allowlist.", { status: 403 });
    }
    const headers = new Headers(request.headers);
    for (const h of STRIPPED_HEADERS) headers.delete(h);
    headers.delete(rule.header);
    const value = await rule.secret();
    headers.set(rule.header, rule.scheme === "" ? value : `${rule.scheme ?? "Bearer"} ${value}`);
    await options.audit("outbound.credentialed", { host: url.hostname, method: request.method, path: url.pathname });
    return options.upstream(new Request(request, { headers }));
  };
}
