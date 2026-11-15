import { describe, expect, it } from "vitest";
import { buildSandboxEnv } from "../../src/index.ts";

const HOST_ENV = {
  PATH: "/usr/bin",
  HOME: "/root",
  OPENROUTER_API_KEY: "sk-or-v1-hostkey000000",
  CLOUDFLARE_API_TOKEN: "cf-token-000000",
  GROQ_API_KEY: "gsk_000000000",
};

/** A fake sandbox: running `env` prints whatever environment the sandbox was given. */
function fakeSandboxEnvOutput(env: Record<string, string>): string {
  return Object.entries(env)
    .map(([k, v]) => `${k}=${v}`)
    .join("\n");
}

describe("sandbox environment guard", () => {
  it("returns none of three host secrets when given a process-like environment", () => {
    const env = buildSandboxEnv({ source: HOST_ENV });
    expect(env).toEqual({ PATH: "/usr/bin", HOME: "/root" });
  });

  it("Covers AE3: a tool that prints its environment shows no secret value anywhere", () => {
    const secrets = [HOST_ENV.OPENROUTER_API_KEY, HOST_ENV.CLOUDFLARE_API_TOKEN, HOST_ENV.GROQ_API_KEY];
    const out = fakeSandboxEnvOutput(buildSandboxEnv({ source: HOST_ENV, secrets }));
    for (const s of secrets) expect(out).not.toContain(s);
    expect(out).toContain("PATH=/usr/bin");
  });

  it("drops an allowlisted variable whose value carries a known secret", () => {
    const env = buildSandboxEnv({
      source: { PATH: "/usr/bin", TZ: "UTC-sk-or-v1-hostkey000000" },
      secrets: ["sk-or-v1-hostkey000000"],
    });
    expect(env).toEqual({ PATH: "/usr/bin" });
  });

  it("includes explicitly set non-secret values", () => {
    expect(buildSandboxEnv({ set: { OC_TASK_ID: "t1" } })).toEqual({ OC_TASK_ID: "t1" });
  });
});
