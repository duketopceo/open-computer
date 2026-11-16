import { describe, expect, it } from "vitest";
import { accountRoute, exchangeAuthCode, type FetchLike } from "../../src/index.ts";

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
});
