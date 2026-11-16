import { env } from "./env.ts";
import { describe, expect, it } from "vitest";
import { OPENROUTER_BASE_URL, ProviderError, type FetchLike } from "@open-computer/agent-core";
import { buildProvider, gatewayConfigured, providerTarget, resolveTarget } from "../src/models/gateway.ts";
import { readSecret, SecretError } from "../src/models/secrets.ts";
import { MODEL_KEY, seedSecrets } from "./support.ts";

describe("provider target", () => {
  it("uses the gateway's OpenRouter route when an account and gateway id are set", () => {
    const t = providerTarget({ AI_GATEWAY_ACCOUNT: "acct123", AI_GATEWAY_ID: "my-gw" });
    expect(t).toEqual({ baseUrl: "https://gateway.ai.cloudflare.com/v1/acct123/my-gw/openrouter", viaGateway: true, source: "account" });
  });

  it("calls OpenRouter directly when no gateway is configured, or only half of it is", () => {
    expect(providerTarget({ AI_GATEWAY_ACCOUNT: "", AI_GATEWAY_ID: "" })).toEqual({ baseUrl: OPENROUTER_BASE_URL, viaGateway: false, source: "direct" });
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

describe("gateway authorization", () => {
  const sseDone = () => new Response('data: {"choices":[{"delta":{"content":"hi"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n');

  async function headersSeen(extra: Record<string, unknown>): Promise<Headers> {
    await seedSecrets();
    let seen = new Headers();
    const fetchImpl: FetchLike = async (_url, init) => {
      seen = new Headers(init.headers);
      return sseDone();
    };
    const provider = buildProvider({ ...env, ...extra } as typeof env, fetchImpl);
    for await (const _ of provider({ model: "m", messages: [{ role: "user", content: "x" }] })) void _;
    return seen;
  }

  it("adds cf-aig-authorization from the secret store when a gateway and its token are configured", async () => {
    const h = await headersSeen({ AI_GATEWAY_ACCOUNT: "acct", AI_GATEWAY_ID: "gw", AIG_TOKEN: { get: async () => "cf-admin-token-value" } });
    expect(h.get("cf-aig-authorization")).toBe("Bearer cf-admin-token-value");
    expect(h.get("authorization")).toBe(`Bearer ${MODEL_KEY}`);
  });

  it("sends no gateway header on the direct route, or when no token is bound", async () => {
    expect((await headersSeen({ AIG_TOKEN: { get: async () => "t" } })).get("cf-aig-authorization")).toBeNull();
    expect((await headersSeen({ AI_GATEWAY_ACCOUNT: "acct", AI_GATEWAY_ID: "gw" })).get("cf-aig-authorization")).toBeNull();
  });
});

describe("the Workers AI binding path", () => {
  const FAKE_ACCOUNT = "binding-supplied-account";
  const binding = (url: string | Error) =>
    ({
      gateway: (id: string) => ({
        getUrl: async (provider?: string) => {
          if (url instanceof Error) throw url;
          return `${url.replace("{id}", id)}/${provider ?? ""}`;
        },
      }),
    }) as unknown as Ai;
  const base = { AI_GATEWAY_ACCOUNT: "", AI_GATEWAY_ID: "open-computer" };

  it("reads the gateway address from the binding, so no account id is configured anywhere", async () => {
    const e = { ...base, AI: binding(`https://gateway.ai.cloudflare.com/v1/${FAKE_ACCOUNT}/{id}`) };
    expect(gatewayConfigured(e)).toBe(true);
    expect(await resolveTarget(e)).toEqual({ baseUrl: `https://gateway.ai.cloudflare.com/v1/${FAKE_ACCOUNT}/open-computer/openrouter`, viaGateway: true, source: "binding" });
  });

  it("prefers the binding over an account id, and falls back to the account route and then direct when the binding fails", async () => {
    const both = { AI_GATEWAY_ACCOUNT: "acct", AI_GATEWAY_ID: "gw", AI: binding("https://gateway.ai.cloudflare.com/v1/from-binding/{id}") };
    expect((await resolveTarget(both)).source).toBe("binding");
    const broken = { ...both, AI: binding(new Error("no gateway")) };
    expect(await resolveTarget(broken)).toMatchObject({ source: "account", viaGateway: true });
    expect(await resolveTarget({ ...base, AI: binding(new Error("no gateway")) })).toEqual({ baseUrl: OPENROUTER_BASE_URL, viaGateway: false, source: "direct" });
  });

  it("is not configured with only half of it", () => {
    expect(gatewayConfigured({ AI_GATEWAY_ACCOUNT: "", AI_GATEWAY_ID: "" })).toBe(false);
    expect(gatewayConfigured({ AI_GATEWAY_ACCOUNT: "", AI_GATEWAY_ID: "gw" })).toBe(false);
    expect(gatewayConfigured({ AI_GATEWAY_ACCOUNT: "a", AI_GATEWAY_ID: "" })).toBe(false);
  });

  it("sends model calls to the binding's address and never puts the key in the URL", async () => {
    await seedSecrets();
    const urls: string[] = [];
    const fetchImpl: FetchLike = async (url) => {
      urls.push(url);
      return new Response('data: {"choices":[{"delta":{"content":"hi"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n', { headers: { "content-type": "text/event-stream" } });
    };
    const e = { ...env, ...base, AI: binding(`https://gateway.ai.cloudflare.com/v1/${FAKE_ACCOUNT}/{id}`) };
    for await (const part of buildProvider(e as typeof env, fetchImpl)({ model: "m/x", messages: [{ role: "user", content: "hi" }] })) void part;
    expect(urls[0]).toBe(`https://gateway.ai.cloudflare.com/v1/${FAKE_ACCOUNT}/open-computer/openrouter/chat/completions`);
    expect(urls[0]).not.toContain(MODEL_KEY);
  });
});
