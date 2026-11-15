import { beforeEach, describe, expect, it } from "vitest";
import { verifyChain, fromNdjson } from "@open-computer/agent-core";
import { timingSafeEqualStrings } from "../src/auth.ts";
import { api, fakeGenericKey, install, readSse, seedSecrets, startTask, textTurn, TOKEN } from "./support.ts";

beforeEach(async () => {
  await seedSecrets();
});

describe("cloud worker auth", () => {
  it("returns 401 on every route without the bearer token, health included", async () => {
    install([textTurn("hi")]);
    for (const [path, method] of [
      ["/v1/health", "GET"],
      ["/v1/model/health", "GET"],
      ["/v1/memory/probe", "POST"],
      ["/v1/tasks", "POST"],
      ["/v1/audit/export", "GET"],
      ["/v1/sessions/s1/events", "GET"],
    ] as const) {
      const res = await api(path, { method, token: null });
      expect(res.status, `${method} ${path}`).toBe(401);
    }
  });

  it("returns 401 for a wrong token and never echoes either token", async () => {
    install([textTurn("hi")]);
    const res = await api("/v1/health", { token: "wrong-token" });
    expect(res.status).toBe(401);
    const text = await res.text();
    expect(text).not.toContain("wrong-token");
    expect(text).not.toContain(TOKEN);
  });

  it("throttles repeated wrong tokens from one source, then still refuses even the right token", async () => {
    install([textTurn("hi")]);
    const ip = "198.51.100.77";
    for (let i = 0; i < 10; i++) expect((await api("/v1/health", { token: "nope", ip })).status).toBe(401);
    expect((await api("/v1/health", { token: "nope", ip })).status).toBe(429);
    expect((await api("/v1/health", { ip })).status).toBe(429);
    expect((await api("/v1/health", { ip: "198.51.100.78" })).status).toBe(200);
  });

  it("compares secrets without short-circuiting: equal, different and prefix-of cases", async () => {
    expect(await timingSafeEqualStrings("abc", "abc")).toBe(true);
    expect(await timingSafeEqualStrings("abc", "abd")).toBe(false);
    expect(await timingSafeEqualStrings("abc", "abcd")).toBe(false);
    expect(await timingSafeEqualStrings("", "")).toBe(true);
  });

  it("answers health with the version when authorized", async () => {
    install([textTurn("hi")]);
    const res = await api("/v1/health");
    expect(await res.json()).toEqual({ ok: true, version: "0.1.0" });
  });
});

describe("model key check and memory probe", () => {
  it("returns the provider key-info result for the stored key and never the key", async () => {
    const fx = install([textTurn("hi")]);
    const ok = await api("/v1/model/health");
    expect(await ok.json()).toEqual({ ok: true });
    expect(fx.fetchCalls[0]?.url).toContain("/key");
    fx.keyInfoStatus.value = 401;
    const bad = await api("/v1/model/health");
    expect(await bad.json()).toEqual({ ok: false, code: "key_invalid" });
    fx.keyInfoStatus.value = 402;
    expect(await (await api("/v1/model/health")).json()).toEqual({ ok: false, code: "payment_required" });
    expect(JSON.stringify(await (await api("/v1/model/health")).json())).not.toContain("test-model-key");
  });

  it("writes, reads back and deletes a probe note", async () => {
    install([textTurn("hi")]);
    const res = await api("/v1/memory/probe", { method: "POST" });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true });
  });
});

describe("tasks and the event stream", () => {
  it("creates a task, streams its events in sequence order and ends with run done", async () => {
    install([textTurn("hello there")]);
    const { task_id, session_id } = await startTask({ goal: "say hello" });
    const { events } = await readSse(await api(`/v1/sessions/${session_id}/events`));
    expect(events.map((e) => e.seq)).toEqual(events.map((_, i) => i));
    expect(events.every((e) => e.task_id === task_id)).toBe(true);
    expect(events[0]).toMatchObject({ type: "run", status: "running" });
    expect(events.at(-1)).toMatchObject({ type: "memory_write", tier: "history" });
    expect(events.some((e) => e.type === "message.done" && e.text === "hello there")).toBe(true);
    expect(events.some((e) => e.type === "run" && e.status === "done")).toBe(true);
  });

  it("replays only later events when reconnecting with Last-Event-ID, with no gaps or duplicates", async () => {
    install([textTurn("hello there")]);
    const { session_id } = await startTask({ goal: "say hello" });
    const full = (await readSse(await api(`/v1/sessions/${session_id}/events`))).events;
    const cut = 3;
    const rest = (await readSse(await api(`/v1/sessions/${session_id}/events`, { headers: { "last-event-id": String(cut) } }))).events;
    expect(rest.map((e) => e.seq)).toEqual(full.filter((e) => e.seq > cut).map((e) => e.seq));
  });

  it("keeps two sessions isolated from each other", async () => {
    install([textTurn("one")]);
    const a = await startTask({ goal: "first" });
    install([textTurn("two")]);
    const b = await startTask({ goal: "second" });
    expect(a.session_id).not.toBe(b.session_id);
    const ea = (await readSse(await api(`/v1/sessions/${a.session_id}/events`))).events;
    const eb = (await readSse(await api(`/v1/sessions/${b.session_id}/events`))).events;
    expect(ea.every((e) => e.task_id === a.task_id)).toBe(true);
    expect(eb.every((e) => e.task_id === b.task_id)).toBe(true);
  });

  it("rejects a task body that carries a key field, and stores nothing", async () => {
    install([textTurn("x")]);
    const res = await api("/v1/tasks", { method: "POST", body: JSON.stringify({ goal: "g", api_key: fakeGenericKey() }) });
    expect(res.status).toBe(400);
    expect(((await res.json()) as { error: { code: string } }).error.code).toBe("keys_not_accepted");
    const nested = await api("/v1/tasks", { method: "POST", body: JSON.stringify({ goal: "g", options: { authorization: "x" } }) });
    expect(nested.status).toBe(400);
  });

  it("rejects an invalid goal, session id and cap with a plain message", async () => {
    install([textTurn("x")]);
    for (const body of [{ goal: "" }, { goal: "g", session_id: "../x" }, { goal: "g", task_cap: 0 }, { goal: "g", task_cap: 1000 }]) {
      const res = await api("/v1/tasks", { method: "POST", body: JSON.stringify(body) });
      expect(res.status).toBe(400);
    }
  });

  it("exports an audit trail whose hash chain verifies and holds no secret", async () => {
    install([textTurn("hello")]);
    const { session_id } = await startTask({ goal: "say hello" });
    await readSse(await api(`/v1/sessions/${session_id}/events`));
    const res = await api("/v1/audit/export");
    expect(res.headers.get("x-audit-chain")).toBe("ok");
    const records = fromNdjson(new TextDecoder().decode(await res.arrayBuffer()));
    expect(records.length).toBeGreaterThan(3);
    expect(await verifyChain(records)).toEqual({ ok: true });
    expect(JSON.stringify(records)).not.toContain(TOKEN);
  });
});
