import defaults from "../../../contracts/defaults.json" with { type: "json" };

/**
 * The private profile overlay (U25): an owner's own defaults, kept outside the repository, layered over the public ones.
 * Only these three values can be overlaid, anything else is refused, and the public file is never edited.
 */
export interface Defaults {
  default_model: string;
  worker_model: string;
  daily_cap_usd: number;
  max_daily_cap_usd: number;
}

export class ProfileError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ProfileError";
  }
}

export const PUBLIC_DEFAULTS: Defaults = { ...defaults };

const MODEL = /^[A-Za-z0-9._:/-]{1,100}$/;

export function applyProfile(text: string | undefined, base: Defaults = PUBLIC_DEFAULTS): Defaults {
  if (text === undefined || text.trim() === "") return { ...base };
  let v: unknown;
  try {
    v = JSON.parse(text);
  } catch {
    throw new ProfileError("The profile is not valid JSON.");
  }
  if (typeof v !== "object" || v === null || Array.isArray(v)) throw new ProfileError("The profile must be a JSON object.");
  const o = v as Record<string, unknown>;
  const out = { ...base };
  for (const k of Object.keys(o)) if (!["default_model", "worker_model", "daily_cap_usd"].includes(k)) throw new ProfileError(`The profile cannot set "${k}".`);
  for (const k of ["default_model", "worker_model"] as const) {
    if (o[k] === undefined) continue;
    if (typeof o[k] !== "string" || !MODEL.test(o[k] as string)) throw new ProfileError(`${k} must be a model name such as vendor/model.`);
    out[k] = o[k] as string;
  }
  if (o.daily_cap_usd !== undefined) {
    const c = o.daily_cap_usd;
    if (typeof c !== "number" || !Number.isFinite(c) || c <= 0 || c > base.max_daily_cap_usd) throw new ProfileError(`daily_cap_usd must be above 0 and at most ${base.max_daily_cap_usd}.`);
    out.daily_cap_usd = c;
  }
  return out;
}
