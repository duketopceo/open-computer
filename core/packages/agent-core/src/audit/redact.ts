const KEY_SHAPES: RegExp[] = [
  /sk-or-v1-[A-Za-z0-9]{8,}/g,
  /\bsk-[A-Za-z0-9_-]{16,}/g,
  /\bBearer\s+[A-Za-z0-9._~+/=-]{8,}/gi,
];

const SENSITIVE_KEY = /authorization|api[-_]?key|token|secret|password|credential/i;

export const REDACTED = "[redacted]";

export interface Redactor {
  redact<T>(value: T): T;
  /** Adds a secret value learned after construction (for example a freshly loaded key). */
  addSecret(value: string): void;
}

/** Removes configured secret values, common key shapes and values under sensitive field names, at any depth. */
export function createRedactor(secrets: string[] = []): Redactor {
  const known = new Set(secrets.filter((s) => s.length >= 6));

  const scrubString = (text: string): string => {
    let out = text;
    for (const secret of known) out = out.split(secret).join(REDACTED);
    for (const shape of KEY_SHAPES) out = out.replace(shape, REDACTED);
    return out;
  };

  const walk = (value: unknown, key?: string): unknown => {
    if (typeof value === "string") return key && SENSITIVE_KEY.test(key) ? REDACTED : scrubString(value);
    if (Array.isArray(value)) return value.map((v) => walk(v));
    if (value && typeof value === "object") {
      const out: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(value as Record<string, unknown>)) out[k] = walk(v, k);
      return out;
    }
    return value;
  };

  return {
    redact: <T>(value: T): T => walk(value) as T,
    addSecret: (s) => {
      if (s.length >= 6) known.add(s);
    },
  };
}
