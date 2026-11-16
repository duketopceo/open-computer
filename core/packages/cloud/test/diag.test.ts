import { env } from "./env.ts";
import { beforeEach, describe, expect, it } from "vitest";
import type { FetchLike } from "@open-computer/agent-core";
import type { Doubles } from "./entry.ts";
import { api, install, MODEL_KEY, seedSecrets, TOKEN } from "./support.ts";

beforeEach(async () => {
  await seedSecrets();
});

const doubles = (): Doubles => (globalThis as unknown as { __doubles: Doubles }).__doubles;
const setFetch = (fn: FetchLike): void => void (doubles().fetch = fn);

const sse = (chunks: unknown[]): Response =>
  new Response(chunks.map((c) => `data: ${JSON.stringify(c)}\n\n`).join("") + "data: [DONE]\n\n", { headers: { "content-type": "text/event-stream" } });

const toolTurnSse = () =>
  sse([
    { choices: [{ delta: { reasoning: "thinking about it" } }] },
    { choices: [{ delta: { tool_calls: [{ index: 0, id: "c1", type: "function", function: { name: "diag_echo", arguments: '{"word":"ping"}' } }] } }] },
    { choices: [{ delta: {}, finish_reason: "tool_calls" }], usage: { prompt_tokens: 10, completion_tokens: 6, cost: 0.002, completion_tokens_details: { reasoning_tokens: 3 } } },
  ]);
const textTurnSse = () =>
  sse([
    { choices: [{ delta: { content: "Done." } }] },
    { choices: [{ delta: {}, finish_reason: "stop" }], usage: { prompt_tokens: 20, completion_tokens: 3, cost: 0.001, completion_tokens_details: { reasoning_tokens: 0 } } },
  ]);

describe("setup page", () => {
  it("is served without a token, carries no secret, and uses no colour hue", async () => {
    install([]);
    const res = await api("/", { token: null });
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toContain("text/html");
    expect(res.headers.get("content-security-policy")).toContain("default-src 'none'");
    const html = await res.text();
    expect(html).toContain("Open Computer: setup and tests");
    expect(html).not.toContain(TOKEN);
    expect(html).not.toContain(MODEL_KEY);
    expect(html).not.toMatch(/purple|violet|indigo/i);
    expect((await api("/setup", { token: null })).status).toBe(200);
  });

  it("still answers 401 on every data route without the token", async () => {
    install([]);
    for (const [path, method] of [
      ["/v1/diag/bindings", "GET"],
      ["/v1/diag/openrouter/auth", "GET"],
      ["/v1/diag/openrouter/completion", "POST"],
      ["/v1/diag/perf", "POST"],
      ["/v1/diag/sandbox", "POST"],
      ["/v1/diag/memory", "POST"],
      ["/v1/diag/audit", "POST"],
      ["/v1/diag/stream", "GET"],
    ] as const) {
      expect((await api(path, { method, token: null })).status, path).toBe(401);
    }
  });
});

describe("diagnostics routes", () => {
  it("reports bindings without any secret value", async () => {
    install([]);
    const res = await api("/v1/diag/bindings");
    const text = await res.text();
    const b = JSON.parse(text);
    expect(b.d1).toBe(true);
    expect(b.r2).toBe(true);
    expect(b.model_key).toBe(true);
    expect(b.durable_objects).toBe(true);
    expect(b.workflow).toBe(true);
    expect(b.container).toBe(false);
    expect(text).not.toContain(MODEL_KEY);
    expect(text).not.toContain(TOKEN);
  });

  it("checks the OpenRouter key and returns only allowlisted numbers", async () => {
    install([]);
    setFetch(async () => new Response(JSON.stringify({ data: { label: "sk-or-v1-abc...xyz", limit: 10, usage: 1.5, limit_remaining: 8.5, is_free_tier: false } })));
    const text = await (await api("/v1/diag/openrouter/auth")).text();
    const b = JSON.parse(text);
    expect(b).toMatchObject({ ok: true, limit: 10, usage: 1.5, limit_remaining: 8.5 });
    expect(text).not.toContain("sk-or");
  });

  it("reads key info through the SDK when the response matches its schema", async () => {
    install([]);
    const full = {
      data: {
        allowed_data_regions: [], budgets: [], byok_usage: 0, byok_usage_daily: 0, byok_usage_monthly: 0, byok_usage_weekly: 0,
        creator_user_id: null, effective_budget: null, free_model_daily_requests: { limit: 50, used: 0, remaining: 50, reset_at: null },
        include_byok_in_limit: false, is_free_tier: false, is_management_key: false, is_provisioning_key: false,
        label: "sk-or-v1-abc...xyz", limit: 10, limit_remaining: 8.5, limit_reset: null, organization_id: null,
        rate_limit: { interval: "10s", note: "deprecated", requests: -1 }, usage: 1.5, usage_daily: 0.1, usage_monthly: 1.5,
        usage_weekly: 0.5, workspace_id: null,
      },
    };
    const urls: string[] = [];
    const auths: Array<string | null> = [];
    setFetch(async (url, init) => {
      urls.push(url);
      auths.push(new Headers(init.headers).get("authorization"));
      return new Response(JSON.stringify(full), { headers: { "content-type": "application/json" } });
    });
    const text = await (await api("/v1/diag/openrouter/auth")).text();
    expect(JSON.parse(text)).toMatchObject({ ok: true, limit: 10, usage: 1.5, limit_remaining: 8.5 });
    expect(urls).toHaveLength(1);
    expect(auths).toEqual([`Bearer ${MODEL_KEY}`]);
    expect(text).not.toContain("sk-or");
  });

  it("maps a rejected key and an exhausted limit to plain codes", async () => {
    install([]);
    setFetch(async () => new Response("{}", { status: 401 }));
    expect(await (await api("/v1/diag/openrouter/auth")).json()).toMatchObject({ ok: false, code: "key_invalid" });
    setFetch(async () => new Response(JSON.stringify({ data: { limit: 5, usage: 5, limit_remaining: 0 } })));
    expect(await (await api("/v1/diag/openrouter/auth")).json()).toMatchObject({ ok: false, code: "payment_required" });
  });

  it("flags a missing default model", async () => {
    install([]);
    setFetch(async () => new Response(JSON.stringify({ data: [{ id: "z-ai/glm-5.3-flash", supported_parameters: ["tools", "reasoning"] }] })));
    const b = (await (await api("/v1/diag/openrouter/models")).json()) as { ok: boolean; models: Array<{ id: string; available: boolean; tools: boolean }> };
    expect(b.ok).toBe(false);
    expect(b.models.find((m) => m.id === "z-ai/glm-5.3")?.available).toBe(false);
    expect(b.models.find((m) => m.id === "z-ai/glm-5.3-flash")).toMatchObject({ available: true, tools: true });
  });

  it("runs a streamed completion with a tool round trip, records spend and an audit entry, and never leaks the key", async () => {
    install([]);
    let calls = 0;
    const seenAuth: string[] = [];
    setFetch(async (_url, init) => {
      seenAuth.push(new Headers(init.headers).get("authorization") ?? "");
      calls += 1;
      return calls === 1 ? toolTurnSse() : textTurnSse();
    });
    const res = await api("/v1/diag/openrouter/completion", { method: "POST" });
    const text = await res.text();
    const b = JSON.parse(text);
    expect(b).toMatchObject({ ok: true, tool_calls: 1, reasoning_tokens: 3, completion_tokens: 9 });
    expect(b.cost_usd).toBeCloseTo(0.003, 6);
    expect(b.reasoning_sample).toContain("thinking");
    expect(b.ttft_ms).not.toBeNull();
    expect(text).not.toContain(MODEL_KEY);
    expect(calls).toBe(2);
    const spent = await env.DB.prepare("SELECT COALESCE(SUM(amount),0) AS s FROM spend WHERE task_id = 'diag'").first<{ s: number }>();
    expect(spent?.s).toBeCloseTo(0.003, 6);
    const audit = await (await api("/v1/audit/export")).text();
    expect(audit).toContain("diag.model_call");
  });

  it("makes no model call when the daily cap is already used", async () => {
    install([]);
    let calls = 0;
    setFetch(async () => {
      calls += 1;
      return toolTurnSse();
    });
    await api("/v1/settings", { method: "PUT", body: JSON.stringify({ daily_cap_usd: 0.01 }) });
    await env.DB.prepare("INSERT INTO spend (step_id, task_id, day, amount) VALUES ('x', 't', ?, 0.02)").bind(new Date().toISOString().slice(0, 10)).run();
    const b = (await (await api("/v1/diag/openrouter/completion", { method: "POST" })).json()) as { ok: boolean; code: string };
    expect(b).toMatchObject({ ok: false, code: "cap_reached" });
    expect(calls).toBe(0);
    await api("/v1/settings", { method: "PUT", body: JSON.stringify({ daily_cap_usd: null }) });
    await env.DB.prepare("DELETE FROM spend WHERE step_id = 'x'").run();
  });

  it("measures D1 and R2 and leaves no scratch data behind", async () => {
    install([]);
    setFetch(async () => new Response("{}"));
    const b = (await (await api("/v1/diag/perf", { method: "POST" })).json()) as Record<string, any>;
    expect(b.ok).toBe(true);
    expect(b.d1_write_ms.n).toBe(10);
    expect(b.d1_read_ms.p95).toBeGreaterThanOrEqual(b.d1_read_ms.p50);
    expect(b.r2_put_mib_per_s).toBeGreaterThan(0);
    expect(b.gateway_overhead_ms).toBeNull();
    const left = await env.DB.prepare("SELECT COUNT(*) AS c FROM settings WHERE key LIKE 'diag:%'").first<{ c: number }>();
    expect(left?.c).toBe(0);
    expect((await env.ARTIFACTS.list({ prefix: "diag/" })).objects).toHaveLength(0);
  });

  it("runs the sandbox check on a fresh box, destroys it, and passes when no key shows", async () => {
    install([]);
    let destroyed = 0;
    let made = 0;
    doubles().sandbox = () => {
      made += 1;
      return {
        exec: async (cmd: string) => ({ stdout: cmd === "env" ? "PATH=/bin\nHOME=/root\n" : cmd.includes("curl") ? "000\n000" : "oc-sandbox-ok\n", stderr: "", exitCode: 0 }),
        readFile: async () => "",
        writeFile: async () => {},
        destroy: async () => void (destroyed += 1),
      };
    };
    const res = (await (await api("/v1/diag/sandbox", { method: "POST" })).json()) as Record<string, any>;
    expect(res).toMatchObject({ ok: true, key_visible: false, egress_blocked: true });
    expect(made).toBe(1);
    expect(destroyed).toBe(1);
  });

  it("fails the sandbox check when the model key shows up in the environment", async () => {
    install([]);
    doubles().sandbox = () => ({
      exec: async (cmd: string) => ({ stdout: cmd === "env" ? `PATH=/bin\nFOO=${MODEL_KEY}\n` : "oc-sandbox-ok\n", stderr: "", exitCode: 0 }),
      readFile: async () => "",
      writeFile: async () => {},
      destroy: async () => {},
    });
    const b = (await (await api("/v1/diag/sandbox", { method: "POST" })).json()) as Record<string, any>;
    expect(b).toMatchObject({ ok: false, code: "key_visible", key_visible: true });
  });

  it("fails the sandbox check when outbound requests succeed", async () => {
    install([]);
    doubles().sandbox = () => ({
      exec: async (cmd: string) => ({ stdout: cmd === "env" ? "PATH=/bin\n" : cmd.includes("curl") ? "200\n200" : "oc-sandbox-ok\n", stderr: "", exitCode: 0 }),
      readFile: async () => "",
      writeFile: async () => {},
      destroy: async () => {},
    });
    const b = (await (await api("/v1/diag/sandbox", { method: "POST" })).json()) as Record<string, any>;
    expect(b).toMatchObject({ ok: false, code: "egress_open", egress_blocked: false });
  });

  it("writes, recalls with a citation, and deletes a memory note", async () => {
    install([]);
    const b = (await (await api("/v1/diag/memory", { method: "POST" })).json()) as Record<string, any>;
    expect(b).toMatchObject({ ok: true, citation: "task:diag" });
    const left = await env.DB.prepare("SELECT COUNT(*) AS c FROM memory_history WHERE task_id = 'diag'").first<{ c: number }>();
    expect(left?.c).toBe(0);
  });

  it("appends an audit entry, verifies the chain, and shows spend and the cap", async () => {
    install([]);
    const b = (await (await api("/v1/diag/audit", { method: "POST" })).json()) as Record<string, any>;
    expect(b).toMatchObject({ ok: true, daily_cap_usd: 5 });
    expect(b.entries).toBeGreaterThanOrEqual(1);
  });

  it("streams ten events for the round trip test", async () => {
    install([]);
    const text = await (await api("/v1/diag/stream?n=10")).text();
    expect(text.split("\n\n").filter((x) => x.startsWith("id:"))).toHaveLength(10);
  });
});
