import defaults from "../../../contracts/defaults.json" with { type: "json" };

import type { RiskClass } from "./events.ts";
import type { PolicyDecision } from "./approvals/policy.ts";

/**
 * The IT policy file (R22). A deployer sets it as JSON text (the `POLICY_JSON` variable on Cloudflare, `OC_POLICY_FILE`
 * or `OC_POLICY_JSON` on a self-hosted core). Every field is optional; an unknown field or a wrong type fails closed, so
 * a typo can never silently loosen a rule.
 *
 *   { "max_daily_cap_usd": 20,
 *     "allowed_models": ["z-ai/glm-5.3", "z-ai/glm-5.3-flash"],
 *     "deny_tools": ["sandbox.exec"],
 *     "risk": { "network": "deny", "write": "ask" },
 *     "locked_settings": ["daily_cap_usd"],
 *     "otlp_endpoint": "https://otel.example.com/v1/traces" }
 */
export interface ItPolicy {
  maxDailyCapUsd: number | null;
  /** When set, a task may only use these models. */
  allowedModels: string[] | null;
  denyTools: string[];
  /** Per risk class decision that replaces the default (read allow, others ask). */
  risk: Partial<Record<RiskClass, PolicyDecision>>;
  /** User settings the person cannot change. */
  lockedSettings: string[];
  otlpEndpoint: string | null;
}

export class ItPolicyError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ItPolicyError";
  }
}

export const NO_IT_POLICY: ItPolicy = { maxDailyCapUsd: null, allowedModels: null, denyTools: [], risk: {}, lockedSettings: [], otlpEndpoint: null };

const FIELDS = new Set(["max_daily_cap_usd", "allowed_models", "deny_tools", "risk", "locked_settings", "otlp_endpoint"]);
const RISKS = ["read", "write", "network", "exec"];
const DECISIONS = ["allow", "ask", "deny"];
export const LOCKABLE_SETTINGS = ["daily_cap_usd", "model", "memory_enabled", "cloud_url"] as const;

function stringList(v: unknown, field: string, nonEmpty: boolean): string[] {
  if (!Array.isArray(v) || v.some((x) => typeof x !== "string" || x === "")) throw new ItPolicyError(`${field} must be a list of non-empty text values.`);
  if (nonEmpty && v.length === 0) throw new ItPolicyError(`${field} cannot be empty; leave it out to allow everything.`);
  return v as string[];
}

/** Empty text means no policy. Anything present must be valid: a broken policy fails closed, it is never skipped. */
export function parseItPolicy(text: string | undefined): ItPolicy {
  if (text === undefined || text.trim() === "") return NO_IT_POLICY;
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    throw new ItPolicyError("The policy is not valid JSON.");
  }
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new ItPolicyError("The policy must be a JSON object.");
  const o = value as Record<string, unknown>;
  for (const k of Object.keys(o)) if (!FIELDS.has(k)) throw new ItPolicyError(`The policy has an unknown field "${k}".`);
  const out: ItPolicy = { ...NO_IT_POLICY, risk: {}, denyTools: [], lockedSettings: [] };
  if (o.max_daily_cap_usd !== undefined) {
    const max = o.max_daily_cap_usd;
    if (typeof max !== "number" || !Number.isFinite(max) || max <= 0) throw new ItPolicyError("max_daily_cap_usd must be a number above 0.");
    out.maxDailyCapUsd = max;
  }
  if (o.allowed_models !== undefined) out.allowedModels = stringList(o.allowed_models, "allowed_models", true);
  if (o.deny_tools !== undefined) out.denyTools = stringList(o.deny_tools, "deny_tools", false);
  if (o.locked_settings !== undefined) {
    out.lockedSettings = stringList(o.locked_settings, "locked_settings", false);
    for (const s of out.lockedSettings) if (!(LOCKABLE_SETTINGS as readonly string[]).includes(s)) throw new ItPolicyError(`locked_settings cannot name "${s}".`);
  }
  if (o.risk !== undefined) {
    if (typeof o.risk !== "object" || o.risk === null || Array.isArray(o.risk)) throw new ItPolicyError("risk must be an object.");
    for (const [k, d] of Object.entries(o.risk as Record<string, unknown>)) {
      if (!RISKS.includes(k)) throw new ItPolicyError(`risk has an unknown class "${k}".`);
      if (typeof d !== "string" || !DECISIONS.includes(d)) throw new ItPolicyError(`risk.${k} must be allow, ask or deny.`);
      out.risk[k as RiskClass] = d as PolicyDecision;
    }
  }
  if (o.otlp_endpoint !== undefined) {
    let u: URL | null = null;
    try {
      u = typeof o.otlp_endpoint === "string" ? new URL(o.otlp_endpoint) : null;
    } catch {
      u = null;
    }
    if (!u || u.protocol !== "https:") throw new ItPolicyError("otlp_endpoint must be an https address.");
    out.otlpEndpoint = u.toString();
  }
  return out;
}

/** Why a model is refused by the policy, or null when it is allowed. */
export function modelRefusal(policy: ItPolicy, model: string): string | null {
  if (policy.allowedModels === null || policy.allowedModels.includes(model)) return null;
  return `Your IT policy allows only these models: ${policy.allowedModels.join(", ")}.`;
}

/** Why a settings change is refused by the policy, or null when it is allowed. */
export function settingRefusal(policy: ItPolicy, key: string): string | null {
  return policy.lockedSettings.includes(key) ? `"${key}" is locked by your IT policy.` : null;
}

/** A user's own cap must be a positive number no larger than the sane bound, which catches typos like 5000. */
export function validateUserDailyCap(value: unknown): number | string {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) return "daily_cap_usd must be a number above 0, or null to use the default.";
  if (value > defaults.max_daily_cap_usd) return `daily_cap_usd cannot be more than ${defaults.max_daily_cap_usd}.`;
  return value;
}

export interface DailyCaps {
  /** What the user chose, or null when they never set one. */
  user: number | null;
  /** The IT policy maximum, or null when there is none. */
  policyMax: number | null;
  /** The cap that applies: the user's (else the default), held under the policy maximum. */
  effective: number;
}

export function resolveDailyCaps(user: number | null, policy: ItPolicy): DailyCaps {
  const base = user ?? defaults.daily_cap_usd;
  const effective = policy.maxDailyCapUsd === null ? base : Math.min(base, policy.maxDailyCapUsd);
  return { user, policyMax: policy.maxDailyCapUsd, effective };
}
