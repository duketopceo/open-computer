import { env } from "./env.ts";
import { beforeEach, describe, expect, it } from "vitest";
import { D1MemoryStore } from "../src/storage/d1-memory.ts";
import { api, install, seedSecrets } from "./support.ts";

beforeEach(async () => {
  await seedSecrets();
});

describe("memory stats and clearing", () => {
  it("refuses without the access token", async () => {
    install([]);
    expect((await api("/v1/memory/stats", { token: null })).status).toBe(401);
    expect((await api("/v1/memory/clear", { method: "POST", token: null })).status).toBe(401);
  });

  it("reports sizes and counts only, never the text", async () => {
    install([]);
    const store = new D1MemoryStore(env.DB);
    await store.setBlock("agent_notes", "prefers short answers");
    await store.addHistory({ id: "task:1", taskId: "1", title: "Secret project plan", summary: "private summary text", ts: "2026-11-15T10:00:00.000Z" });
    const res = await api("/v1/memory/stats");
    const text = await res.text();
    expect(JSON.parse(text)).toEqual({
      blocks: [
        { name: "agent_notes", chars: 21 },
        { name: "user_profile", chars: 0 },
      ],
      entries: 1,
      newest: "2026-11-15T10:00:00.000Z",
    });
    expect(text).not.toContain("Secret project plan");
    expect(text).not.toContain("private summary text");
  });

  it("clears both tiers, records it in the audit log, and the stats then read empty", async () => {
    install([]);
    const store = new D1MemoryStore(env.DB);
    await store.setBlock("user_profile", "likes tea");
    await store.addHistory({ id: "task:2", taskId: "2", title: "t", summary: "s", ts: "2026-11-15T11:00:00.000Z" });
    const res = await api("/v1/memory/clear", { method: "POST" });
    const out = (await res.json()) as { ok: boolean; cleared: number };
    expect(out.ok).toBe(true);
    expect(out.cleared).toBeGreaterThanOrEqual(1);
    expect(await store.getBlock("user_profile")).toBe("");
    expect(await store.listHistory(10)).toEqual([]);
    expect(await (await api("/v1/memory/stats")).json()).toMatchObject({ entries: 0, newest: null });
    expect(await (await api("/v1/audit/export")).text()).toContain("memory.cleared");
  });
});
