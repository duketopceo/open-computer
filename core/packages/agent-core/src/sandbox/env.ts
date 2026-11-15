/** Names a sandbox process may see by default. Anything else, including every key, is left out. */
export const DEFAULT_ENV_ALLOWLIST = ["PATH", "HOME", "LANG", "TERM", "TZ"] as const;

export interface SandboxEnvInput {
  /** Environment of the host process; only allowlisted names are read from it. */
  source?: Record<string, string | undefined>;
  allow?: readonly string[];
  /** Explicit values the caller chooses to set (non-secret configuration). */
  set?: Record<string, string>;
  /** Known secret values; a variable carrying one of them is dropped even when allowlisted. */
  secrets?: readonly string[];
}

/** Builds a sandbox environment from an allowlist instead of inheriting the host's. */
export function buildSandboxEnv(input: SandboxEnvInput = {}): Record<string, string> {
  const allow = new Set(input.allow ?? DEFAULT_ENV_ALLOWLIST);
  const secrets = (input.secrets ?? []).filter((s) => s.length >= 6);
  const out: Record<string, string> = {};
  for (const name of allow) {
    const v = input.source?.[name];
    if (typeof v === "string") out[name] = v;
  }
  for (const [name, v] of Object.entries(input.set ?? {})) out[name] = v;
  for (const [name, v] of Object.entries(out)) {
    if (secrets.some((s) => v.includes(s))) delete out[name];
  }
  return out;
}
