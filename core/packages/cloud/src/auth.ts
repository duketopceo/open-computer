import type { Env } from "./env.ts";
import { readSecret, SecretError } from "./models/secrets.ts";
import { ledgerStub } from "./storage/ledger-client.ts";

/** Compares two strings without short-circuiting on the first differing byte (digest both, XOR every byte). */
export async function timingSafeEqualStrings(a: string, b: string): Promise<boolean> {
  const enc = new TextEncoder();
  const [da, db] = await Promise.all([
    crypto.subtle.digest("SHA-256", enc.encode(a)),
    crypto.subtle.digest("SHA-256", enc.encode(b)),
  ]);
  const x = new Uint8Array(da);
  const y = new Uint8Array(db);
  let diff = 0;
  for (let i = 0; i < x.length; i++) diff |= (x[i] as number) ^ (y[i] as number);
  return diff === 0 && a.length === b.length;
}

export type AuthResult = "ok" | "unauthorized" | "blocked" | "unconfigured";

/** Bearer check against the access secret, with repeated failures from one source throttled. */
export async function authenticate(request: Request, env: Env): Promise<AuthResult> {
  const source = request.headers.get("cf-connecting-ip") ?? "unknown";
  const ledger = ledgerStub(env);
  if (await ledger.authBlocked(source)) return "blocked";
  let secret: string;
  try {
    secret = await readSecret(env.ACCESS_SECRET, "access_secret");
  } catch (e) {
    if (e instanceof SecretError) return "unconfigured";
    throw e;
  }
  const header = request.headers.get("authorization") ?? "";
  const given = header.startsWith("Bearer ") ? header.slice(7) : "";
  if (given !== "" && (await timingSafeEqualStrings(given, secret))) return "ok";
  await ledger.authFailed(source);
  return "unauthorized";
}
