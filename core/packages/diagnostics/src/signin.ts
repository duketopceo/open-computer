// "Sign in with Cloudflare". The person clicks Allow in their browser on Cloudflare's own consent page; nobody mints or
// pastes a token. This runs Wrangler's documented OAuth login (PKCE, a localhost callback, the same flow `wrangler login`
// uses) with exactly the scopes setup needs. Wrangler keeps and refreshes the session itself; this code never reads it.
// A hidden "Advanced / CI" fallback remains: set CLOUDFLARE_API_TOKEN and no sign-in is attempted.

/** What provisioning and deploy need. R2 buckets are covered by the Workers scopes. */
export const REQUIRED_SCOPES = [
  "account:read",
  "user:read",
  "workers:write",
  "workers_scripts:write",
  "workers_routes:write",
  "workers_tail:read",
  "d1:write",
  "secrets_store:write",
  "containers:write",
  "cloudchamber:write",
] as const;

/** Scopes the OAuth consent page cannot grant today (checked with `wrangler login --scopes-list`). */
export const NOT_AVAILABLE_BY_OAUTH = ["AI Gateway (create gateway)", "Access (apps and policies)"] as const;

/** The Wrangler arguments that start the browser sign-in. */
export function signInArgs(): string[] {
  return ["login", "--scopes", ...REQUIRED_SCOPES];
}

export interface SignInState {
  signedIn: boolean;
  /** The Cloudflare user's email, shown so the person sees which account they approved. */
  email: string | null;
  accounts: string[];
  scopes: string[];
  missing: string[];
  /** True when CLOUDFLARE_API_TOKEN is in use (the Advanced / CI fallback), so no sign-in is needed. */
  viaToken: boolean;
}

/** Reads `wrangler whoami` output into names only: no account id, no token. */
export function parseWhoami(text: string, viaToken = false): SignInState {
  const signedIn = /You are logged in/i.test(text) && !/not authenticated|not logged in/i.test(text);
  const email = /associated with the email ([^\s.]+(?:\.[^\s.]+)*@[^\s]+?)\.?\s/i.exec(text)?.[1] ?? null;
  const accounts: string[] = [];
  for (const line of text.split("\n")) {
    const m = /^│\s*(.+?)\s*│\s*[0-9a-f]{32}\s*│/.exec(line);
    if (m && m[1] && m[1] !== "Account Name") accounts.push(m[1]);
  }
  const scopes = [...text.matchAll(/^- ([a-z0-9_.-]+)\s*(?:\((?:read|write|edit|run|admin)\))?/gim)].map((m) => m[1] as string);
  const have = new Set(scopes);
  const missing = viaToken ? [] : REQUIRED_SCOPES.filter((s) => !have.has(s.split(":")[0] as string) && !have.has(s));
  return { signedIn, email, accounts, scopes, missing, viaToken };
}
