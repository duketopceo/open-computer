import { beforeEach, describe, expect, it } from "vitest";
import type { FetchLike } from "@open-computer/agent-core";
import { env } from "./env.ts";
import type { Doubles } from "./entry.ts";
import { loadPriceBook, parsePrices, PRICE_TTL_MS } from "../src/models/prices.ts";
import { install, readSse, seedSecrets, startTask, textTurn, api } from "./support.ts";

const MODELS = {
  data: [
    { id: "z-ai/glm-5.3", pricing: { prompt: "0.0000006", completion: "0.0000022" } },
    { id: "free/model", pricing: { prompt: "0", completion: "0" } },
    { id: "openrouter/auto", pricing: { prompt: "-1", completion: "-1" } },
    { id: "broken", pricing: { prompt: "x" } },
  ],
};

const respond = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

beforeEach(async () => {
  await seedSecrets();
  await env.DB.prepare("DELETE FROM settings WHERE key IN ('price_book','price_book_at')").run();
});

describe("parsePrices", () => {
  it("turns per-token strings into dollars per million tokens and drops variable or broken prices", () => {
    const t = parsePrices(MODELS);
    expect(t["z-ai/glm-5.3"]?.[0]).toBeCloseTo(0.6, 6);
    expect(t["z-ai/glm-5.3"]?.[1]).toBeCloseTo(2.2, 6);
    expect(t["free/model"]).toEqual([0, 0]);
    expect(t["openrouter/auto"]).toBeUndefined();
    expect(t.broken).toBeUndefined();
  });
  it("returns an empty table for anything that is not a models list", () => {
    expect(parsePrices(null)).toEqual({});
    expect(parsePrices({ data: "x" })).toEqual({});
  });
});

describe("loadPriceBook", () => {
  it("fetches once, caches in D1, and serves the cache until it is a day old", async () => {
    let calls = 0;
    const f: FetchLike = async () => (calls++, respond(MODELS));
    const t0 = 1_000_000;
    expect((await loadPriceBook(env.DB, f, t0)).price("z-ai/glm-5.3")?.inPerMTok).toBeCloseTo(0.6, 6);
    await loadPriceBook(env.DB, f, t0 + PRICE_TTL_MS - 1);
    expect(calls).toBe(1);
    await loadPriceBook(env.DB, f, t0 + PRICE_TTL_MS + 1);
    expect(calls).toBe(2);
  });

  it("keeps the stale copy when a refresh fails, and is empty when there never was one", async () => {
    const t0 = 5_000_000;
    await loadPriceBook(env.DB, async () => respond(MODELS), t0);
    const stale = await loadPriceBook(env.DB, async () => respond({}, 500), t0 + PRICE_TTL_MS + 5);
    expect(stale.price("z-ai/glm-5.3")).not.toBeNull();
    await env.DB.prepare("DELETE FROM settings WHERE key IN ('price_book','price_book_at')").run();
    const none = await loadPriceBook(env.DB, async () => { throw new TypeError("down"); }, t0);
    expect(none.price("z-ai/glm-5.3")).toBeNull();
  });
});

describe("small caps", () => {
  const setFetch = (fn: FetchLike): void => void ((globalThis as unknown as { __doubles: Doubles }).__doubles.fetch = fn);

  it("a 5 cent task cap no longer pauses for approval once real prices are known", async () => {
    install([textTurn("ok")]);
    setFetch(async () => respond(MODELS));
    const { session_id } = await startTask({ goal: "say ok", task_cap: 0.05 });
    const { events } = await readSse(await api(`/v1/sessions/${session_id}/events`));
    expect(events.some((e) => e.type === "approval")).toBe(false);
    expect(events.at(-1)).toMatchObject({ type: "memory_write" });
    expect(events.some((e) => e.type === "run" && e.status === "done")).toBe(true);
  });

  it("without prices the same cap still pauses, so the conservative default is intact", async () => {
    install([textTurn("ok")]);
    setFetch(async () => respond({}, 500));
    const { session_id } = await startTask({ goal: "say ok", task_cap: 0.05 });
    const { events } = await readSse(await api(`/v1/sessions/${session_id}/events`));
    expect(events.some((e) => e.type === "approval")).toBe(true);
  });
});
