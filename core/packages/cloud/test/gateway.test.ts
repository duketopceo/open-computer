import { env } from "./env.ts";
import { describe, expect, it } from "vitest";
import { OPENROUTER_BASE_URL, ProviderError, type FetchLike } from "@open-computer/agent-core";
import { buildProvider, providerTarget } from "../src/models/gateway.ts";
import { readSecret, SecretError } from "../src/models/secrets.ts";
import { MODEL_KEY, seedSecrets } from "./support.ts";

describe("provider target", () => {
  it("uses the gateway's OpenRouter route when an account and gateway id are set", () => {
    const t = providerTarget({ AI_GATEWAY_ACCOUNT: "acct123", AI_GATEWAY_ID: "my-gw" });
    expect(t).toEqual({ baseUrl: "https://gateway.ai.cloudflare.com/v1/acct123/my-gw/openrouter", viaGateway: true });
  });

  it("calls OpenRouter directly when no gateway is configured, or only half of it is", () => {
    expect(providerTarget({ AI_GATEWAY_ACCOUNT: "", AI_GATEWAY_ID: "" })).toEqual({ baseUrl: OPENROUTER_BASE_URL, viaGateway: false });
    expect(providerTarget({ AI_GATEWAY_ACCOUNT: "acct", AI_GATEWAY_ID: "" }).viaGateway).toBe(false);
  });
});

describe("secrets", () => {
  it("returns a stored secret", async () => {
    await seedSecrets();
    expect(await readSecret(env.MODEL_KEY, "model_key")).toBe(MODEL_KEY);
  });

  it("names the slot, and never a value, when a secret is missing", async () => {
    const empty = { get: async () => "" };
    await expect(readSecret(empty, "model_key")).rejects.toBeInstanceOf(SecretError);
    await expect(readSecret({ get: async () => Promise.reject(new Error("store down: hunter2")) }, "model_key")).rejects.toThrow(
      /"model_key" is not set/,
    );
    try {
      await readSecret({ get: async () => Promise.reject(new Error("store down: hunter2")) }, "model_key");
    } catch (e) {
      expect(String(e)).not.toContain("hunter2");
    }
  });
});

describe("provider from the secret store", () => {
  it("sends the stored key as the bearer to the resolved URL and never puts it in the body or the stream", async () => {
    await seedSecrets();
    const calls: Array<{ url: string; init: RequestInit }> = [];
    const fetchImpl: FetchLike = async (url, init) => {
      calls.push({ url, init });
      return new Response('data: {"choices":[{"delta":{"content":"hi"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n', {
        headers: { "content-type": "text/event-stream" },
      });
    };
    const provider = buildProvider({ ...env, AI_GATEWAY_ACCOUNT: "acct", AI_GATEWAY_ID: "gw" }, fetchImpl);
    const parts: unknown[] = [];
    for await (const p of provider({ model: "example/model", messages: [{ role: "user", content: "hi" }] })) parts.push(p);
    expect(calls[0]?.url).toBe("https://gateway.ai.cloudflare.com/v1/acct/gw/openrouter/chat/completions");
    expect((calls[0]?.init.headers as Record<string, string>).authorization).toBe(`Bearer ${MODEL_KEY}`);
    expect(String(calls[0]?.init.body)).not.toContain(MODEL_KEY);
    expect(JSON.stringify(parts)).not.toContain(MODEL_KEY);
  });

  it("fails with a typed missing-secret error that names the slot when no key is stored", async () => {
    const noKey = { ...env, MODEL_KEY: { get: async () => "" } as unknown as SecretsStoreSecret };
    const provider = buildProvider(noKey, async () => new Response(""));
    const err = await (async () => {
      for await (const _ of provider({ model: "m", messages: [] })) void _;
    })().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ProviderError);
    expect(err).toMatchObject({ code: "key_missing" });
  });
});
