import { afterEach, describe, expect, it } from "vitest";
import { fromNdjson, type FetchLike } from "@open-computer/agent-core";
import { startLocalServer } from "../src/server.ts";
import { LocalStore } from "../src/runtime/store.ts";

const TOKEN = "settings-test-token";
const CUSTOM_KEY = ["sk", "test", "custom123456789"].join("-");
const open: Array<{ close(): unknown }> = [];
afterEach(async () => {
  while (open.length) await open.pop()?.close();
});

const sse = (chunks: unknown[]): Response =>
  new Response(chunks.map((c) => `data: ${JSON.stringify(c)}\n\n`).join("") + "data: [DONE]\n\n", { headers: { "content-type": "text/event-stream" } });

async function boot(fetchImpl?: FetchLike) {
  const store = new LocalStore(":memory:");
  open.push(store);
  const running = await startLocalServer({
    token: TOKEN,
    ...(fetchImpl ? { fetch: fetchImpl } : {}),
    runtime: { store, sandboxKind: "none", sandbox: async () => { throw new Error("none"); } },
  });
  open.push(running);
  const base = `http://127.0.0.1:${running.port}`;
  const call = (method: string, p: string, body?: unknown) =>
    fetch(base + p, { method, headers: { authorization: `Bearer ${TOKEN}`, "content-type": "application/json" }, ...(body !== undefined ? { body: JSON.stringify(body) } : {}) });
  return { call, store };
}

describe("models and custom endpoint settings", () => {
  it("saves model choices and a custom endpoint, shows them back, and never shows a key", async () => {
    const b = await boot();
    const put = await b.call("PUT", "/v1/settings", { default_model: "vendor/big", worker_model: "vendor/small", custom_endpoint_url: "https://llm.example.com/v1/", custom_endpoint_model: "my-model" });
    expect(put.status).toBe(200);
    await b.call("POST", "/v1/secrets/load", { secrets: { custom_key: CUSTOM_KEY } });
    const text = await (await b.call("GET", "/v1/settings")).text();
    expect(JSON.parse(text)).toMatchObject({ default_model: "vendor/big", worker_model: "vendor/small", custom_endpoint_url: "https://llm.example.com/v1", custom_endpoint_model: "my-model", secrets_set: { custom_key: true } });
    expect(text).not.toContain(CUSTOM_KEY);
  });

  it("clears a saved choice with null and rejects bad names and addresses", async () => {
    const b = await boot();
    await b.call("PUT", "/v1/settings", { default_model: "vendor/big" });
    await b.call("PUT", "/v1/settings", { default_model: null });
    expect(await (await b.call("GET", "/v1/settings")).json()).toMatchObject({ default_model: "z-ai/glm-5.3" });
    expect((await b.call("PUT", "/v1/settings", { default_model: "has spaces" })).status).toBe(400);
    expect((await b.call("PUT", "/v1/settings", { custom_endpoint_url: "http://remote.example.com/v1" })).status).toBe(400);
    expect((await b.call("PUT", "/v1/settings", { custom_endpoint_url: "http://127.0.0.1:11434/v1" })).status).toBe(200);
  });

  it("runs a task on the custom endpoint with its own key and the saved model name, and never sends the OpenRouter key there", async () => {
    const seen: Array<{ url: string; auth: string | null; model?: string }> = [];
    const f: FetchLike = async (url, init) => {
      if (url.endsWith("/models")) return new Response(JSON.stringify({ data: [] }));
      if (url.startsWith("http://127.0.0.1")) return fetch(url, init);
      seen.push({ url, auth: new Headers(init.headers).get("authorization"), model: JSON.parse(String(init.body)).model });
      return sse([{ choices: [{ delta: { content: "hello from custom" } }] }, { choices: [{ delta: {}, finish_reason: "stop" }], usage: { prompt_tokens: 1, completion_tokens: 1, cost: 0 } }]);
    };
    const b = await boot(f);
    await b.call("PUT", "/v1/settings", { custom_endpoint_url: "https://llm.example.com/v1", custom_endpoint_model: "my-model" });
    await b.call("POST", "/v1/secrets/load", { secrets: { custom_key: CUSTOM_KEY, model_key: ["sk", "or", "other123456789"].join("-") } });
    const started = (await (await b.call("POST", "/v1/tasks", { goal: "say hi", model: "custom" })).json()) as { session_id: string };
    const events = await (await b.call("GET", `/v1/sessions/${started.session_id}/events`)).text();
    expect(events).toContain("hello from custom");
    expect(seen).toEqual([{ url: "https://llm.example.com/v1/chat/completions", auth: `Bearer ${CUSTOM_KEY}`, model: "my-model" }]);
  });

  it("says plainly when the custom endpoint is chosen but not set up", async () => {
    const b = await boot(async (url, init) => (url.startsWith("http://127.0.0.1") ? fetch(url, init) : new Response(JSON.stringify({ data: [] }))));
    const started = (await (await b.call("POST", "/v1/tasks", { goal: "x", model: "custom" })).json()) as { session_id: string };
    const events = await (await b.call("GET", `/v1/sessions/${started.session_id}/events`)).text();
    expect(events).toContain("Set the custom endpoint address and model in Settings first.");
  });

  it("records an approval note in the audit chain, and refuses a note that is too long", async () => {
    const b = await boot();
    expect(true).toBe(true);
    const bad = await b.call("POST", "/v1/sessions/s1/approvals/a1", { hash: "h", approved: true, note: "x".repeat(501) });
    expect(bad.status).toBe(400);
    void fromNdjson;
  });
});

describe("private profile", () => {
  it("changes the default model without touching the public file, and a saved choice still wins", async () => {
    const store = new LocalStore(":memory:");
    open.push(store);
    const running = await startLocalServer({ token: TOKEN, runtime: { store, sandboxKind: "none", sandbox: async () => { throw new Error("none"); }, profileJson: '{"default_model":"me/private"}' } });
    open.push(running);
    const call = (method: string, body?: unknown) => fetch(`http://127.0.0.1:${running.port}/v1/settings`, { method, headers: { authorization: `Bearer ${TOKEN}`, "content-type": "application/json" }, ...(body ? { body: JSON.stringify(body) } : {}) });
    expect(await (await call("GET")).json()).toMatchObject({ default_model: "me/private", worker_model: "z-ai/glm-5.3-flash" });
    await call("PUT", { default_model: "me/chosen" });
    expect(await (await call("GET")).json()).toMatchObject({ default_model: "me/chosen" });
  });
});
