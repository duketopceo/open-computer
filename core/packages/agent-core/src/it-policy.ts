import defaults from "../../../contracts/defaults.json" with { type: "json" };

/**
 * The IT policy as far as this slice reads it: a maximum daily spend cap that overrides the user's own setting.
 * The full lockable policy file (R22) arrives with U23; this is its first field, read from the `POLICY_JSON`
 * variable a deployer sets.
 */
export interface ItPolicy {
  maxDailyCapUsd: number | null;
}

export class ItPolicyError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ItPolicyError";
  }
}

export const NO_IT_POLICY: ItPolicy = { maxDailyCapUsd: null };

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
  const max = (value as Record<string, unknown>).max_daily_cap_usd;
  if (max === undefined) return NO_IT_POLICY;
  if (typeof max !== "number" || !Number.isFinite(max) || max <= 0) throw new ItPolicyError("max_daily_cap_usd must be a number above 0.");
  return { maxDailyCapUsd: max };
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
