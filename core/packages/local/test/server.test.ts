import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";
import type { FetchLike } from "@open-computer/agent-core";
import { startLocalServer, type RunningServer } from "../src/server.ts";

const TOKEN = "per-launch-token-for-tests";
const fixture = (name: string): unknown =>
  JSON.parse(readFileSync(fileURLToPath(new URL(`../../../contracts/local-api/${name}.json`, import.meta.url)), "utf8"));

const open: RunningServer[] = [];
afterEach(async () => {
  while (open.length) await open.pop()?.close();
});

/** A fake cloud core: maps path to a JSON response, or throws for network failure. */
function fakeCloud(routes: Record<string, { status?: number; body: unknown } | "unreachable" | "hang">): FetchLike {
  return async (url, init) => {
    const path = new URL(url).pathname;
    const spec = routes[path];
    if (spec === "unreachable" || !spec) throw new TypeError("fetch failed");
    if (spec === "hang") {
      await new Promise((_, reject) => init.signal?.addEventListener("abort", () => reject(new DOMException("t", "TimeoutError"))));
    }
    const s = spec as { status?: number; body: unknown };
    return new Response(JSON.stringify(s.body), { status: s.status ?? 200 });
  };
}

async function boot(fetchImpl?: FetchLike, extra: Partial<Parameters<typeof startLocalServer>[0]> = {}) {
  const running = await startLocalServer({ token: TOKEN, ...(fetchImpl ? { fetch: fetchImpl } : {}), ...extra });
  open.push(running);
  const base = `http://127.0.0.1:${running.port}`;
  const req = (path: string, init: RequestInit = {}, token: string | null = TOKEN) =>
    fetch(base + path, {
      ...init,
      headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}) },
    });
  return { running, req };
}

const CLOUD_OK = fakeCloud({
  "/v1/health": { body: { ok: true } },
  "/v1/model/health": { body: { ok: true } },
  "/v1/memory/probe": { body: { ok: true } },
});

async function configured(fetchImpl: FetchLike, settings: object = {}) {
  const b = await boot(fetchImpl);
  await b.req("/v1/secrets/load", { method: "POST", body: JSON.stringify({ secrets: { cloud_access: "placeholder-not-a-real-secret" } }) });
  await b.req("/v1/settings", {
    method: "PUT",
    body: JSON.stringify({ cloud_url: "https://core.example.com", model: "example/model", memory_enabled: true, ...settings }),
  });
  return b;
}

describe("local core server", () => {
  it("returns 401 on every path, health included, without the token, and the body names no secret", async () => {
    const { req } = await boot();
    for (const [path, method] of [["/v1/health", "GET"], ["/v1/settings", "GET"], ["/v1/test-connection", "POST"], ["/v1/secrets/load", "POST"]] as const) {
      const res = await req(path, { method, ...(method === "POST" ? { body: "{}" } : {}) }, null);
      expect(res.status).toBe(401);
      expect(await res.json()).toEqual(fixture("error-unauthorized"));
    }
  });

  it("returns 401 for a wrong token", async () => {
    const { req } = await boot();
    const res = await req("/v1/health", {}, "not-the-token");
    expect(res.status).toBe(401);
    expect(JSON.stringify(await res.json())).not.toContain(TOKEN);
  });

  it("answers health with the version, matching the contract fixture", async () => {
    const { req } = await boot();
    expect(await (await req("/v1/health")).json()).toEqual(fixture("health"));
  });

  it("loads secrets into memory only: the response and every later read omit the values", async () => {
    const { req } = await boot();
    const load = fixture("secrets-load-request") as { secrets: Record<string, string> };
    const res = await req("/v1/secrets/load", { method: "POST", body: JSON.stringify(load) });
    expect(await res.json()).toEqual(fixture("secrets-load-response"));
    const everything = [
      JSON.stringify(await (await req("/v1/settings")).json()),
      JSON.stringify(await (await req("/v1/health")).json()),
      JSON.stringify(await (await req("/v1/test-connection", { method: "POST" })).json()),
    ].join("");
    for (const v of Object.values(load.secrets)) expect(everything).not.toContain(v);
  });

  it("accepts a settings write and returns exactly the settings fixture, with secret flags only", async () => {
    const { req } = await boot();
    await req("/v1/secrets/load", { method: "POST", body: JSON.stringify({ secrets: { cloud_access: "placeholder-not-a-real-secret" } }) });
    const put = await req("/v1/settings", { method: "PUT", body: JSON.stringify(fixture("settings-put-request")) });
    expect(await put.json()).toEqual(fixture("settings"));
    expect(await (await req("/v1/settings")).json()).toEqual(fixture("settings"));
  });

  it("rejects an unknown setting field and a non-boolean memory flag", async () => {
    const { req } = await boot();
    const a = await req("/v1/settings", { method: "PUT", body: JSON.stringify({ colour: "red" }) });
    expect(a.status).toBe(400);
    const b = await req("/v1/settings", { method: "PUT", body: JSON.stringify({ memory_enabled: "yes" }) });
    expect(b.status).toBe(400);
  });

  it("test connection with everything healthy equals the ok fixture", async () => {
    const { req } = await configured(CLOUD_OK);
    expect(await (await req("/v1/test-connection", { method: "POST" })).json()).toEqual(fixture("test-connection-ok"));
  });

  it("Covers AE1: a rejected model key fails only the model piece; backend and memory still pass", async () => {
    const cloud = fakeCloud({
      "/v1/health": { body: { ok: true } },
      "/v1/model/health": { body: { ok: false, code: "key_invalid" } },
      "/v1/memory/probe": { body: { ok: true } },
    });
    const { req } = await configured(cloud);
    const body = await (await req("/v1/test-connection", { method: "POST" })).json();
    expect(body).toEqual(fixture("test-connection-model-key-rejected"));
  });

  it("names an unreachable backend in plain words and marks the other pieces as blocked by it", async () => {
    const { req } = await configured(fakeCloud({ "/v1/health": "unreachable" }));
    const body = (await (await req("/v1/test-connection", { method: "POST" })).json()) as Record<string, { ok: boolean; code: string; message: string }>;
    expect(body.backend).toMatchObject({ ok: false, code: "backend_unreachable" });
    expect(body.model).toMatchObject({ ok: false, code: "blocked_by_backend" });
    expect(body.memory).toMatchObject({ ok: false, code: "blocked_by_backend" });
    expect(JSON.stringify(body)).not.toMatch(/TypeError|fetch failed/);
  });

  it("times out a hanging backend well inside the whole request budget", async () => {
    const b = await boot(fakeCloud({ "/v1/health": "hang" }), { checkTimeoutMs: 40 });
    await b.req("/v1/secrets/load", { method: "POST", body: JSON.stringify({ secrets: { cloud_access: "placeholder-not-a-real-secret" } }) });
    await b.req("/v1/settings", { method: "PUT", body: JSON.stringify({ cloud_url: "https://core.example.com" }) });
    const started = Date.now();
    const body = (await (await b.req("/v1/test-connection", { method: "POST" })).json()) as Record<string, { code: string }>;
    expect(body.backend?.code).toBe("backend_timeout");
    expect(Date.now() - started).toBeLessThan(2000);
  });

  it("reports a memory probe failure while the other two pieces keep their own results", async () => {
    const cloud = fakeCloud({
      "/v1/health": { body: { ok: true } },
      "/v1/model/health": { body: { ok: true } },
      "/v1/memory/probe": { status: 500, body: { ok: false } },
    });
    const { req } = await configured(cloud);
    const body = (await (await req("/v1/test-connection", { method: "POST" })).json()) as Record<string, { ok: boolean; code: string }>;
    expect(body.backend?.ok).toBe(true);
    expect(body.model?.ok).toBe(true);
    expect(body.memory).toMatchObject({ ok: false, code: "memory_failed" });
  });

  it("refuses to bind a non-loopback address even when asked", async () => {
    await expect(startLocalServer({ token: TOKEN, host: "0.0.0.0" })).rejects.toThrow(/loopback/);
  });

  it("binds to loopback only", async () => {
    const { running } = await boot();
    const addr = running.server.address();
    expect(typeof addr === "object" && addr?.address).toBe("127.0.0.1");
  });
});
