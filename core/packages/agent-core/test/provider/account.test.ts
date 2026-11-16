import { describe, expect, it } from "vitest";
import { accountCredits, accountKey, accountRoute, exchangeAuthCode, type FetchLike } from "../../src/index.ts";

describe("OpenRouter SDK account helpers (fake server)", () => {
  it("exchanges a PKCE code for a key through the SDK and sends the verifier", async () => {
    const seen: Array<{ url: string; body: string }> = [];
    const fetchImpl: FetchLike = async (url, init) => {
      seen.push({ url, body: new TextDecoder().decode(init.body as ArrayBuffer) });
      return new Response(JSON.stringify({ key: "sk-or-fake-1", user_id: "u1" }), { headers: { "content-type": "application/json" } });
    };
    expect(await exchangeAuthCode({ fetch: fetchImpl }, "code123", "verifier456")).toBe("sk-or-fake-1");
    expect(seen[0]?.url).toContain("/auth/keys");
    expect(seen[0]?.body).toContain("code123");
    expect(seen[0]?.body).toContain("verifier456");
  });

  it("returns null for a rejected exchange and a null reply for an unknown route", async () => {
    const reject: FetchLike = async () => new Response("{}", { status: 400 });
    expect(await exchangeAuthCode({ fetch: reject }, "c", "v")).toBeNull();
    expect(await accountRoute("nope", { apiKey: "k", fetch: reject })).toBeNull();
  });

  it("a 403 on credits means a management key is needed, on the SDK path and on the raw fallback; only 401 means a rejected key", async () => {
    const forbidden: FetchLike = async () => new Response('{"error":{"code":403,"message":"Only management keys can fetch credits for an account"}}', { status: 403, headers: { "content-type": "application/json" } });
    expect(await accountCredits({ apiKey: "k", fetch: forbidden })).toEqual({ status: 200, body: { available: false, reason: "management_key_required" } });

    // First answer: a 200 body the SDK's schema rejects (forces the raw fallback). Second answer, the raw read: 403.
    let n = 0;
    const odd: FetchLike = async () => (++n === 1 ? new Response('{"data":{"unexpected":true}}', { status: 200, headers: { "content-type": "application/json" } }) : new Response("{}", { status: 403 }));
    expect(await accountCredits({ apiKey: "k", fetch: odd })).toEqual({ status: 200, body: { available: false, reason: "management_key_required" } });
    expect(n).toBe(2);

    const rejected: FetchLike = async () => new Response("{}", { status: 401 });
    expect((await accountCredits({ apiKey: "k", fetch: rejected })).body).toMatchObject({ error: { code: "key_invalid" } });
    // A 403 on the key itself is a permission problem, not "rejected".
    expect((await accountKey({ apiKey: "k", fetch: forbidden })).body).toMatchObject({ error: { code: "forbidden" } });
  });

  it("reads the limit reset and period usage the real key endpoint sends", async () => {
    const f: FetchLike = async () => new Response(JSON.stringify({ data: { label: "k", limit: 50, usage: 82.29, limit_remaining: 12.5, limit_reset: "monthly", usage_daily: 1, usage_weekly: 7, usage_monthly: 37.5, is_free_tier: false, include_byok_in_limit: false, created_at: "2026-01-01T00:00:00Z", updated_at: null } }), { headers: { "content-type": "application/json" } });
    const r = await accountKey({ apiKey: "k", fetch: f });
    expect(r.body).toMatchObject({ limit: 50, limit_remaining: 12.5, limit_reset: "monthly", usage_monthly: 37.5 });
  });
});
