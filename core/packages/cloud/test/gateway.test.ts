import { env } from "./env.ts";
import { describe, expect, it } from "vitest";
import { GATEWAY_NEEDS_AUTH, OPENROUTER_BASE_URL, ProviderError, type FetchLike } from "@open-computer/agent-core";
import { BINDING_BASE, buildProvider, gatewayConfigured, gatewayReport, providerTarget, readGatewayState, resolveTarget } from "../src/models/gateway.ts";
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

describe("the Workers AI binding path: which address is chosen", () => {
  const binding = { gateway: () => ({ run: async () => new Response("") }) } as unknown as Ai;
  const base = { AI_GATEWAY_ACCOUNT: "", AI_GATEWAY_ID: "open-computer" };

  it("uses the binding when there is one, with no account id anywhere", async () => {
    const e = { ...base, AI: binding };
    expect(gatewayConfigured(e)).toBe(true);
    expect(await resolveTarget(e)).toEqual({ baseUrl: BINDING_BASE, viaGateway: true, source: "binding" });
  });

  it("prefers the binding over an account id, uses the account route without a binding, and goes direct with neither", async () => {
    expect((await resolveTarget({ AI_GATEWAY_ACCOUNT: "acct", AI_GATEWAY_ID: "gw", AI: binding })).source).toBe("binding");
    expect(await resolveTarget({ AI_GATEWAY_ACCOUNT: "acct", AI_GATEWAY_ID: "gw" })).toMatchObject({ source: "account", viaGateway: true });
    expect(await resolveTarget(base)).toEqual({ baseUrl: OPENROUTER_BASE_URL, viaGateway: false, source: "direct" });
  });

  it("is not configured with only half of it", () => {
    expect(gatewayConfigured({ AI_GATEWAY_ACCOUNT: "", AI_GATEWAY_ID: "" })).toBe(false);
    expect(gatewayConfigured({ AI_GATEWAY_ACCOUNT: "", AI_GATEWAY_ID: "gw" })).toBe(false);
    expect(gatewayConfigured({ AI_GATEWAY_ACCOUNT: "a", AI_GATEWAY_ID: "" })).toBe(false);
  });
});

describe("authenticated gateway: the binding's run path, with a direct retry when the gateway refuses", () => {
  const sseBody = 'data: {"id":"gen-b","choices":[{"delta":{"content":"hel"}}]}\n\ndata: {"id":"gen-b","choices":[{"delta":{"content":"lo"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n';
  const sse = () => new Response(sseBody, { headers: { "content-type": "text/event-stream" } });
  const request = { model: "m/x", messages: [{ role: "user" as const, content: "hi" }] };

  const run = async (e: unknown, f: FetchLike) => {
    const parts: Array<{ t: string; text?: string }> = [];
    for await (const p of buildProvider(e as typeof env, f)(request)) parts.push(p as { t: string; text?: string });
    return parts;
  };
  const text = (parts: Array<{ t: string; text?: string }>) => parts.filter((p) => p.t === "text").map((p) => p.text).join("");

  const fakeBinding = (runImpl: (req: Record<string, unknown>) => Promise<Response>, seen: Array<Record<string, unknown>> = []) =>
    ({ gateway: (id: string) => ({ run: async (req: Record<string, unknown>) => (seen.push({ id, ...req }), runImpl(req)) }) }) as unknown as Ai;

  const noDirect = (urls: string[]): FetchLike => async (url) => {
    urls.push(url);
    return sse();
  };

  it("sends the call through gateway.run with provider, endpoint, headers and the body as query, and streams the answer", async () => {
    await seedSecrets();
    const seen: Array<Record<string, unknown>> = [];
    const direct: string[] = [];
    const e = { ...env, AI_GATEWAY_ACCOUNT: "", AI_GATEWAY_ID: "open-computer", AI: fakeBinding(async () => sse(), seen) };
    const parts = await run(e, noDirect(direct));
    expect(text(parts)).toBe("hello");
    expect(direct).toEqual([]);
    expect(seen).toHaveLength(1);
    expect(seen[0]).toMatchObject({ id: "open-computer", provider: "openrouter", endpoint: "chat/completions" });
    expect((seen[0]?.headers as Record<string, string>).authorization).toBe(`Bearer ${MODEL_KEY}`);
    expect(seen[0]?.query).toMatchObject({ model: "m/x", stream: true });
    expect(JSON.stringify(seen[0]?.query)).not.toContain(MODEL_KEY);
    expect(await readGatewayState(env)).toMatchObject({ ok: true });
  });

  it("an AiGatewayError 401 / 2009 falls back to OpenRouter once, records why, and still answers", async () => {
    await seedSecrets();
    const refusal = Object.assign(new Error("Unauthorized"), { name: "AiGatewayError", httpCode: 401, internalCode: 2009 });
    const direct: string[] = [];
    const auth: string[] = [];
    const f: FetchLike = async (url, init) => {
      direct.push(url);
      auth.push((init.headers as Record<string, string>).authorization ?? "");
      return sse();
    };
    const e = { ...env, AI_GATEWAY_ACCOUNT: "", AI_GATEWAY_ID: "open-computer", AI: fakeBinding(async () => Promise.reject(refusal)) };
    expect(text(await run(e, f))).toBe("hello");
    expect(direct).toEqual([`${OPENROUTER_BASE_URL}/chat/completions`]);
    expect(auth).toEqual([`Bearer ${MODEL_KEY}`]);
    expect(await readGatewayState(env)).toMatchObject({ ok: false, reason: GATEWAY_NEEDS_AUTH });
    // The bindings report says the truth: configured, but calls go direct, and why.
    const report = gatewayReport(e, await resolveTarget(e), await readGatewayState(env));
    expect(report).toMatchObject({ gateway: false, gateway_configured: true, gateway_via: "direct", gateway_reason: GATEWAY_NEEDS_AUTH });
  });

  it("any other gateway failure also falls back once, with its own reason", async () => {
    await seedSecrets();
    const odd = Object.assign(new Error("boom"), { name: "AiGatewayError", httpCode: 500, internalCode: 2001 });
    const direct: string[] = [];
    const e = { ...env, AI_GATEWAY_ACCOUNT: "", AI_GATEWAY_ID: "gw", AI: fakeBinding(async () => Promise.reject(odd)) };
    expect(text(await run(e, noDirect(direct)))).toBe("hello");
    expect(direct).toHaveLength(1);
    expect(await readGatewayState(env)).toMatchObject({ ok: false, reason: "AI Gateway error (HTTP 500, code 2001)." });
    const plain = { ...env, AI_GATEWAY_ACCOUNT: "", AI_GATEWAY_ID: "gw", AI: fakeBinding(async () => Promise.reject(new Error("socket closed"))) };
    expect(text(await run(plain, noDirect([])))).toBe("hello");
  });

  it("a gateway-format 401 body on a plain gateway URL falls back; OpenRouter's own 401 does not", async () => {
    await seedSecrets();
    const gatewayBody = JSON.stringify({ success: false, error: [{ code: 2009, message: "Unauthorized" }] });
    const urls: string[] = [];
    const f: FetchLike = async (url) => {
      urls.push(url);
      return url.startsWith("https://gateway.ai.cloudflare.com/") ? new Response(gatewayBody, { status: 401 }) : sse();
    };
    const e = { ...env, AI_GATEWAY_ACCOUNT: "acct", AI_GATEWAY_ID: "gw" };
    expect(text(await run(e, f))).toBe("hello");
    expect(urls).toEqual(["https://gateway.ai.cloudflare.com/v1/acct/gw/openrouter/chat/completions", `${OPENROUTER_BASE_URL}/chat/completions`]);

    // OpenRouter's own rejection comes back through the gateway untouched and is reported as the key, not the gateway.
    const orUrls: string[] = [];
    const g: FetchLike = async (url) => (orUrls.push(url), new Response(JSON.stringify({ error: { code: 401, message: "No auth credentials found" } }), { status: 401 }));
    await expect(run(e, g)).rejects.toMatchObject({ code: "key_invalid" });
    expect(orUrls).toHaveLength(1);
  });

  it("a refusal that also reaches the model call as a response is mapped to gateway_refused, not key_invalid", async () => {
    await seedSecrets();
    const f: FetchLike = async () => new Response(JSON.stringify({ success: false, error: [{ code: 2009, message: "Unauthorized" }] }), { status: 401 });
    // No gateway configured, so nothing retries: the provider itself must still tell the two apart.
    await expect(run({ ...env, AI_GATEWAY_ACCOUNT: "", AI_GATEWAY_ID: "" }, f)).rejects.toMatchObject({ code: "gateway_refused", message: GATEWAY_NEEDS_AUTH });
  });

  it("a healthy gateway reports itself as in use", async () => {
    const e = { AI_GATEWAY_ACCOUNT: "", AI_GATEWAY_ID: "open-computer", AI: fakeBinding(async () => sse()) };
    expect(gatewayReport(e, await resolveTarget(e), { ok: true, at: "t" })).toMatchObject({ gateway: true, gateway_via: "binding", gateway_configured: true });
  });
});
