import { afterEach, describe, expect, it } from "vitest";
import type { FetchLike } from "@open-computer/agent-core";
import { startLocalServer, type RunningServer } from "../src/server.ts";
import { LocalStore } from "../src/runtime/store.ts";

const TOKEN = "policy-test-token";
const open: Array<{ close(): unknown }> = [];
afterEach(async () => {
  while (open.length) await open.pop()?.close();
});

const sse = (chunks: unknown[]): Response =>
  new Response(chunks.map((c) => `data: ${JSON.stringify(c)}\n\n`).join("") + "data: [DONE]\n\n", { headers: { "content-type": "text/event-stream" } });

async function boot(policyJson: string, fetchImpl?: FetchLike) {
  const store = new LocalStore(":memory:");
  open.push(store);
  const running: RunningServer = await startLocalServer({
    token: TOKEN,
    ...(fetchImpl ? { fetch: fetchImpl } : {}),
    runtime: { store, sandboxKind: "none", sandbox: async () => { throw new Error("none"); }, policyJson },
  });
  open.push(running);
  const base = `http://127.0.0.1:${running.port}`;
  const call = (method: string, p: string, body?: unknown) =>
    fetch(base + p, { method, headers: { authorization: `Bearer ${TOKEN}`, "content-type": "application/json" }, ...(body !== undefined ? { body: JSON.stringify(body) } : {}) });
  return { call, base };
}

describe("the policy file on the self-hosted core", () => {
  it("refuses a model that is not allowed and a locked setting", async () => {
    const b = await boot('{"allowed_models":["z-ai/glm-5.3"],"locked_settings":["daily_cap_usd"]}');
    const refused = await b.call("POST", "/v1/tasks", { goal: "g", model: "other/model" });
    expect(refused.status).toBe(403);
    expect(((await refused.json()) as { error: { code: string } }).error.code).toBe("policy_model");
    const locked = await b.call("PUT", "/v1/settings", { daily_cap_usd: 3 });
    expect(locked.status).toBe(403);
  });

  it("holds the daily cap under max_daily_cap_usd", async () => {
    const b = await boot('{"max_daily_cap_usd":2}');
    expect(await (await b.call("GET", "/v1/settings")).json()).toMatchObject({ effective_daily_cap_usd: 2 });
    await b.call("PUT", "/v1/settings", { daily_cap_usd: 50 });
    expect(await (await b.call("GET", "/v1/settings")).json()).toMatchObject({ effective_daily_cap_usd: 2 });
  });

  it("denies a tool by name without asking and exports a trace without content", async () => {
    const posts: string[] = [];
    let turn = 0;
    const f: FetchLike = async (url, init) => {
      if (url.startsWith("https://otel.example.com")) return posts.push(String(init.body)), new Response("{}");
      if (url.endsWith("/models")) return new Response(JSON.stringify({ data: [] }));
      if (url.endsWith("/chat/completions")) {
        turn += 1;
        return turn === 1
          ? sse([{ choices: [{ delta: { tool_calls: [{ index: 0, id: "c1", type: "function", function: { name: "sandbox.exec", arguments: '{"command":"ls"}' } }] } }] }, { choices: [{ delta: {}, finish_reason: "tool_calls" }], usage: { prompt_tokens: 1, completion_tokens: 1, cost: 0.001 } }])
          : sse([{ choices: [{ delta: { content: "the secret answer" } }] }, { choices: [{ delta: {}, finish_reason: "stop" }], usage: { prompt_tokens: 1, completion_tokens: 1, cost: 0.001 } }]);
      }
      return new Response("{}", { status: 404 });
    };
    const b = await boot('{"deny_tools":["sandbox.exec"],"otlp_endpoint":"https://otel.example.com/v1/traces"}', f);
    // The daemon's key is read lazily; load one so the provider can be built.
    await b.call("POST", "/v1/secrets/load", { secrets: { model_key: ["sk", "test", "policy123456789"].join("-") } });
    const started = (await (await b.call("POST", "/v1/tasks", { goal: "a private goal" })).json()) as { session_id: string };
    const text = await (await b.call("GET", `/v1/sessions/${started.session_id}/events`)).text();
    expect(text).not.toContain('"type":"approval"');
    expect(text).toContain('"status":"denied"');
    for (let i = 0; i < 50 && posts.length === 0; i++) await new Promise((r) => setTimeout(r, 20));
    expect(posts).toHaveLength(1);
    expect(posts[0]).not.toContain("secret answer");
    expect(posts[0]).not.toContain("private goal");
  });
});
