import { beforeEach, describe, expect, it } from "vitest";
import type { FetchLike } from "@open-computer/agent-core";
import type { Doubles } from "./entry.ts";
import { api, install, MODEL_KEY, readSse, seedSecrets, startTask, textTurn, usage } from "./support.ts";

beforeEach(async () => {
  await seedSecrets();
});

const doubles = (): Doubles => (globalThis as unknown as { __doubles: Doubles }).__doubles;
const setFetch = (fn: FetchLike): void => void (doubles().fetch = fn);
const ok = (body: unknown): Response => new Response(JSON.stringify(body), { headers: { "content-type": "application/json" } });

describe("OpenRouter account routes (official SDK behind the core's own auth)", () => {
  it("refuses without the access token", async () => {
    install([]);
    for (const p of ["key", "credits", "models", "generations"]) expect((await api(`/v1/openrouter/${p}`, { token: null })).status, p).toBe(401);
  });

  it("returns key usage and limit, credits and priced models, and never the key", async () => {
    install([]);
    const seen: string[] = [];
    setFetch(async (url, init) => {
      seen.push(`${new Headers(init.headers).get("authorization")} ${url}`);
      if (url.endsWith("/key")) return ok({ data: { label: "sk-or-v1-abc...xyz", limit: 10, usage: 1.5, limit_remaining: 8.5, is_free_tier: false } });
      if (url.endsWith("/credits")) return ok({ data: { total_credits: 20, total_usage: 3.5 } });
      return ok({ data: [{ id: "a/cheap", name: "Cheap", context_length: 128000, pricing: { prompt: "0.0000001", completion: "0.0000004" } }] });
    });
    const key = await (await api("/v1/openrouter/key")).text();
    expect(JSON.parse(key)).toMatchObject({ limit: 10, usage: 1.5, limit_remaining: 8.5, is_free_tier: false });
    expect(JSON.parse(await (await api("/v1/openrouter/credits")).text())).toEqual({ available: true, total: 20, used: 3.5 });
    const models = JSON.parse(await (await api("/v1/openrouter/models")).text()) as { models: Array<{ id: string; prompt_per_m: number }> };
    expect(models.models[0]).toMatchObject({ id: "a/cheap" });
    expect(models.models[0]?.prompt_per_m).toBeCloseTo(0.1, 6);
    expect(seen.every((s) => s.startsWith(`Bearer ${MODEL_KEY} `))).toBe(true);
    expect(key).not.toContain(MODEL_KEY);
  });

  it("tells which models support tools and reasoning, from the provider's supported parameters", async () => {
    install([]);
    setFetch(async () => ok({ data: [{ id: "a/tooly", name: "Tooly", context_length: 1000, pricing: { prompt: "0.000001", completion: "0.000002" }, supported_parameters: ["tools", "reasoning"] }, { id: "b/plain", name: "Plain", context_length: 500, pricing: { prompt: "0.000001", completion: "0.000002" }, supported_parameters: ["temperature"] }] }));
    const body = (await (await api("/v1/openrouter/models")).json()) as { models: Array<Record<string, unknown>> };
    expect(body.models.find((m) => m.id === "a/tooly")).toMatchObject({ tools: true, reasoning: true });
    expect(body.models.find((m) => m.id === "b/plain")).toMatchObject({ tools: false, reasoning: false });
  });

  it("maps a rejected key to a plain 401 code", async () => {
    install([]);
    setFetch(async () => new Response("{}", { status: 401 }));
    const res = await api("/v1/openrouter/key");
    expect(res.status).toBe(401);
    expect(await res.json()).toMatchObject({ error: { code: "key_invalid" } });
  });

  it("says credits need a management key on a 403, instead of calling a working key rejected", async () => {
    install([]);
    setFetch(async () => new Response(JSON.stringify({ error: { code: 403, message: "Only management keys can fetch credits for an account" } }), { status: 403, headers: { "content-type": "application/json" } }));
    const res = await api("/v1/openrouter/credits");
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ available: false, reason: "management_key_required" });
  });

  it("returns the limit reset and period usage with the key, as the real API sends them", async () => {
    install([]);
    setFetch(async () => ok({ data: { label: "k", limit: 50, usage: 82.29, limit_remaining: 12.5, limit_reset: "monthly", usage_daily: 1.1, usage_weekly: 7.7, usage_monthly: 37.5, is_free_tier: false } }));
    expect(await (await api("/v1/openrouter/key")).json()).toMatchObject({ limit: 50, usage: 82.29, limit_remaining: 12.5, limit_reset: "monthly", usage_monthly: 37.5 });
  });
});

describe("recent generations", () => {
  it("is empty before any task has run", async () => {
    install([]);
    expect(await (await api("/v1/openrouter/generations")).json()).toEqual({ generations: [] });
  });

  it("records the generation id from each model call in the event stream and audit, and shows them through the generation endpoint", async () => {
    install([textTurn("hello", usage(0.002), "gen-abc123")]);
    const { session_id } = await startTask({ goal: "say hello" });
    const { events } = await readSse(await api(`/v1/sessions/${session_id}/events`));
    expect(events.find((e) => e.type === "usage")).toMatchObject({ generation_id: "gen-abc123" });
    expect(await (await api("/v1/audit/export")).text()).toContain("gen-abc123");

    const asked: string[] = [];
    setFetch(async (url) => {
      asked.push(url);
      return ok({ data: { id: "gen-abc123", model: "vendor/small", total_cost: 0.002, tokens_prompt: 10, tokens_completion: 5, latency: 800, created_at: "2026-11-15T00:00:00Z" } });
    });
    const body = (await (await api("/v1/openrouter/generations")).json()) as { generations: Array<Record<string, unknown>> };
    expect(body.generations).toHaveLength(1);
    expect(body.generations[0]).toMatchObject({ id: "gen-abc123", model: "vendor/small", cost: 0.002, prompt_tokens: 10, completion_tokens: 5, latency_ms: 800 });
    expect(asked.some((u) => u.includes("generation?id=gen-abc123"))).toBe(true);
  });

  it("keeps the id and says it is pending when OpenRouter has no record yet", async () => {
    install([textTurn("hi", usage(), "gen-late")]);
    const { session_id } = await startTask({ goal: "hi" });
    await readSse(await api(`/v1/sessions/${session_id}/events`));
    setFetch(async () => new Response("{}", { status: 404 }));
    const body = (await (await api("/v1/openrouter/generations")).json()) as { generations: Array<Record<string, unknown>> };
    expect(body.generations[0]).toMatchObject({ id: "gen-late", pending: true });
  });
});
