import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { applyProfile, PUBLIC_DEFAULTS } from "../src/index.ts";

describe("private profile overlay", () => {
  it("returns the public defaults for no profile and layers the three allowed values over them", () => {
    expect(applyProfile(undefined)).toEqual(PUBLIC_DEFAULTS);
    expect(applyProfile("  ")).toEqual(PUBLIC_DEFAULTS);
    expect(applyProfile('{"default_model":"me/big","daily_cap_usd":12}')).toEqual({ ...PUBLIC_DEFAULTS, default_model: "me/big", daily_cap_usd: 12 });
    expect(PUBLIC_DEFAULTS.daily_cap_usd).toBe(5);
  });

  it("refuses anything else, so a profile can never loosen the maximum or smuggle in a key", () => {
    for (const bad of ['{"max_daily_cap_usd":99999}', '{"api_key":"x"}', '{"daily_cap_usd":0}', '{"daily_cap_usd":5000}', '{"default_model":"has spaces"}', "[]", "{not json"]) {
      expect(() => applyProfile(bad), bad).toThrow();
    }
  });
});

describe("public defaults", () => {
  const file = JSON.parse(readFileSync(new URL("../../../contracts/defaults.json", import.meta.url), "utf8")) as Record<string, unknown>;

  it("hold only the four public fields, with the owner's $5 default cap", () => {
    expect(Object.keys(file).sort()).toEqual(["daily_cap_usd", "default_model", "max_daily_cap_usd", "worker_model"]);
    expect(file.daily_cap_usd).toBe(5);
  });

  it("name no host, path, account or person", () => {
    const text = JSON.stringify(file);
    expect(text).not.toMatch(/\/home\/|\.local|@|workers\.dev|\.com|\.ts\b|localhost|[0-9a-f]{32}/i);
  });
});
