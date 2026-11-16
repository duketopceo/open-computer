// Cloudflare admin steps that need a scoped API token (the login Wrangler holds cannot do them): an AI Gateway in front of
// OpenRouter, and Cloudflare Access in front of the core. The token comes from the environment only:
//   OC_CF_ADMIN_TOKEN="$(omaseal get cloudflare oc-admin)" oc admin gateway|access ...
// Required scopes: AI Gateway Edit and Read; Access Apps and Policies Edit. Nothing here runs unless that token is present.

export type Fetch = (url: string, init: RequestInit) => Promise<Response>;

export interface AdminResult {
  ok: boolean;
  /** What happened, in plain words. */
  message: string;
  /** Non-secret identifiers worth keeping (for example the gateway id). */
  details?: Record<string, string>;
}

const API = "https://api.cloudflare.com/client/v4";

async function cf(fetchImpl: Fetch, token: string, method: string, path: string, body?: unknown): Promise<{ status: number; json: any }> {
  const res = await fetchImpl(`${API}${path}`, {
    method,
    headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    signal: AbortSignal.timeout(20_000),
  });
  return { status: res.status, json: await res.json().catch(() => ({})) };
}

const denied = (what: string, scope: string): AdminResult => ({ ok: false, message: `Cloudflare refused ${what}. The token needs the scope "${scope}".` });

/** Creates the AI Gateway (id `open-computer`) when it does not exist. Safe to run again. */
export async function ensureGateway(fetchImpl: Fetch, o: { token: string; accountId: string; id?: string }): Promise<AdminResult> {
  const id = o.id ?? "open-computer";
  const got = await cf(fetchImpl, o.token, "GET", `/accounts/${o.accountId}/ai-gateway/gateways/${id}`);
  if (got.status === 401 || got.status === 403) return denied("reading AI Gateways", "AI Gateway: Read");
  if (got.status === 200 && got.json?.success !== false) return { ok: true, message: `The AI Gateway "${id}" already exists.`, details: { gateway_id: id } };
  const made = await cf(fetchImpl, o.token, "POST", `/accounts/${o.accountId}/ai-gateway/gateways`, {
    id,
    collect_logs: true,
    cache_ttl: 0,
    rate_limiting_interval: 0,
    rate_limiting_limit: 0,
    authentication: true,
  });
  if (made.status === 401 || made.status === 403) return denied("creating an AI Gateway", "AI Gateway: Edit");
  if (made.status >= 200 && made.status < 300 && made.json?.success !== false) return { ok: true, message: `Created the AI Gateway "${id}" (authenticated).`, details: { gateway_id: id } };
  return { ok: false, message: `The gateway could not be created (Cloudflare answered ${made.status}).` };
}

/** Puts Cloudflare Access in front of a hostname and allows only the given email addresses. Safe to run again. */
export async function ensureAccess(fetchImpl: Fetch, o: { token: string; accountId: string; hostname: string; emails: string[]; name?: string }): Promise<AdminResult> {
  if (o.emails.length === 0) return { ok: false, message: "Name at least one email address that may sign in." };
  const name = o.name ?? "Open Computer core";
  const list = await cf(fetchImpl, o.token, "GET", `/accounts/${o.accountId}/access/apps`);
  if (list.status === 401 || list.status === 403) return denied("reading Access applications", "Access: Apps and Policies: Edit");
  const existing = (list.json?.result as Array<{ id: string; domain: string }> | undefined)?.find((a) => a.domain === o.hostname);
  let appId = existing?.id;
  if (!appId) {
    const made = await cf(fetchImpl, o.token, "POST", `/accounts/${o.accountId}/access/apps`, { name, domain: o.hostname, type: "self_hosted", session_duration: "24h" });
    if (made.status === 401 || made.status === 403) return denied("creating an Access application", "Access: Apps and Policies: Edit");
    appId = made.json?.result?.id as string | undefined;
    if (!appId) return { ok: false, message: `The Access application could not be created (Cloudflare answered ${made.status}).` };
  }
  const policy = await cf(fetchImpl, o.token, "POST", `/accounts/${o.accountId}/access/apps/${appId}/policies`, {
    name: `${name}: allowed people`,
    decision: "allow",
    include: o.emails.map((email) => ({ email: { email } })),
  });
  if (policy.status >= 200 && policy.status < 300) return { ok: true, message: `Access now protects ${o.hostname} for ${o.emails.length} email address${o.emails.length === 1 ? "" : "es"}.`, details: { access_app_id: appId } };
  return { ok: false, message: `The Access policy could not be created (Cloudflare answered ${policy.status}).` };
}
