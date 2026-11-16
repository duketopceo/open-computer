import { describe, expect, it } from "vitest";
import { GATEWAY_NEEDS_AUTH, gatewayErrorFromBody, openRouterProvider, type FetchLike } from "../../src/index.ts";

describe("telling a gateway refusal from an OpenRouter rejection", () => {
  it("reads Cloudflare's error shape and nothing else", () => {
    expect(gatewayErrorFromBody('{"success":false,"error":[{"code":2009,"message":"Unauthorized"}]}')).toEqual({ code: 2009, message: "Unauthorized" });
    expect(gatewayErrorFromBody('{"error":{"code":401,"message":"No auth credentials found"}}')).toBeNull();
    expect(gatewayErrorFromBody("not json")).toBeNull();
    expect(gatewayErrorFromBody('{"success":true}')).toBeNull();
  });

  const call = async (f: FetchLike) => {
    const p = openRouterProvider({ fetch: f, baseUrl: "https://example.test/api/v1", apiKey: () => "k" });
    for await (const part of p({ model: "m", messages: [{ role: "user", content: "hi" }] })) void part;
  };

  it("maps a gateway 401 to gateway_refused and an OpenRouter 401 to key_invalid", async () => {
    await expect(call(async () => new Response('{"success":false,"error":[{"code":2009,"message":"Unauthorized"}]}', { status: 401 }))).rejects.toMatchObject({ code: "gateway_refused", message: GATEWAY_NEEDS_AUTH });
    await expect(call(async () => new Response('{"error":{"code":401,"message":"No auth credentials found"}}', { status: 401 }))).rejects.toMatchObject({ code: "key_invalid" });
  });
});
