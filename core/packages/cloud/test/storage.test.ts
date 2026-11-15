import { applyD1Migrations } from "cloudflare:test";
import { env } from "./env.ts";
import { describe, expect, it } from "vitest";
import { verifyChain } from "@open-computer/agent-core";
import { D1MemoryStore, ftsQuery } from "../src/storage/d1-memory.ts";
import { ledgerStub, readAllAudit } from "../src/storage/ledger-client.ts";
import { ArtifactError, R2Artifacts } from "../src/storage/r2-artifacts.ts";

const migrations = env.TEST_MIGRATIONS;

describe("migration", () => {
  it("creates the tables and is a no-op on a second run", async () => {
    await applyD1Migrations(env.DB, migrations);
    const names = (await env.DB.prepare("SELECT name FROM sqlite_master WHERE type IN ('table')").all<{ name: string }>()).results.map((r) => r.name);
    for (const t of ["audit", "spend", "task_cap_extra", "tasks", "memory_blocks", "memory_history"]) expect(names).toContain(t);
  });
});

describe("ledger: audit chain", () => {
  it("forms one verifying chain from appends made through two separate stubs", async () => {
    await ledgerStub(env).appendAudit("note", "t1", { n: 1 });
    await ledgerStub(env).appendAudit("note", "t2", { n: 2 });
    await ledgerStub(env).appendAudit("note", "t1", { n: 3 });
    const records = await readAllAudit(env);
    expect(records.map((r) => r.seq)).toEqual([0, 1, 2]);
    expect(await verifyChain(records)).toEqual({ ok: true });
  });

  it("produces a gap-free sequence and a verifying chain from fifty concurrent appends", async () => {
    const before = (await readAllAudit(env)).length;
    await Promise.all(Array.from({ length: 50 }, (_, i) => env.LEDGER.get(env.LEDGER.idFromName("ledger")).appendAudit("note", `t${i % 4}`, { i })));
    const records = await readAllAudit(env);
    expect(records).toHaveLength(before + 50);
    expect(records.map((r) => r.seq)).toEqual(Array.from({ length: before + 50 }, (_, i) => i));
    expect(await verifyChain(records)).toEqual({ ok: true });
  });

  it("redacts a key-shaped string and an Authorization field before storing", async () => {
    await ledgerStub(env).appendAudit("http", "t", { Authorization: "Bearer abcdefghij123456", note: "key sk-or-v1-abcdef1234567890 inside" });
    const text = JSON.stringify(await readAllAudit(env));
    expect(text).not.toMatch(/abcdefghij123456|sk-or-v1-abcdef/);
  });
});

describe("ledger: spend", () => {
  const reserve = (taskId: string, stepId: string, day: string, amount: number, dayCap: number | null = null, taskCap = 10) =>
    ledgerStub(env).tryReserve({ taskId, stepId, day, amount, taskCap, dayCap });

  it("sums the day across tasks and ignores the previous day", async () => {
    await reserve("a", "a:0", "2026-01-01", 0.06, 0.1);
    expect(await reserve("b", "b:0", "2026-01-01", 0.06, 0.1)).toMatchObject({ ok: false, scope: "day" });
    expect(await reserve("b", "b:0", "2026-01-02", 0.06, 0.1)).toEqual({ ok: true });
    expect(await ledgerStub(env).daySpent("2026-01-01")).toBeCloseTo(0.06, 6);
  });

  it("records a step's spend once when the same step is written twice", async () => {
    await reserve("t", "t:0", "2026-01-01", 0.4);
    await reserve("t", "t:0", "2026-01-01", 0.4);
    expect(await ledgerStub(env).taskSpent("t")).toBeCloseTo(0.4, 6);
  });

  it("lets exactly one of two concurrent reservations through when together they would pass the day cap", async () => {
    const [a, b] = await Promise.all([reserve("ca", "ca:0", "2026-01-03", 0.06, 0.1), reserve("cb", "cb:0", "2026-01-03", 0.06, 0.1)]);
    expect([a.ok, b.ok].filter(Boolean)).toHaveLength(1);
  });

  it("settles a reservation to the actual cost and keeps it when the cost is unknown", async () => {
    await reserve("t", "t:0", "2026-01-04", 0.3);
    await ledgerStub(env).settle({ stepId: "t:0", actual: 0.01 });
    expect(await ledgerStub(env).taskSpent("t")).toBeCloseTo(0.01, 6);
    await reserve("u", "u:0", "2026-01-04", 0.3);
    await ledgerStub(env).settle({ stepId: "u:0", actual: null });
    expect(await ledgerStub(env).taskSpent("u")).toBeCloseTo(0.3, 6);
  });

  it("adds a task cap extra that persists", async () => {
    await ledgerStub(env).addTaskCapExtra("t", 0.03);
    await ledgerStub(env).addTaskCapExtra("t", 0.02);
    expect(await ledgerStub(env).taskCapExtra("t")).toBeCloseTo(0.05, 6);
  });
});

describe("D1 memory store", () => {
  it("round-trips a block", async () => {
    const store = new D1MemoryStore(env.DB);
    await store.setBlock("agent_notes", "likes tables");
    await store.setBlock("agent_notes", "likes tables and short answers");
    expect(await store.getBlock("agent_notes")).toBe("likes tables and short answers");
    expect(await store.getBlock("user_profile")).toBe("");
  });

  it("ranks a stored summary by keyword, best match first, and replaces an entry with the same id", async () => {
    const store = new D1MemoryStore(env.DB);
    await store.addHistory({ id: "task:1", taskId: "1", title: "quarterly invoice export", summary: "Goal: export invoices. Outcome: csv", ts: "2026-01-01" });
    await store.addHistory({ id: "task:2", taskId: "2", title: "birthday party", summary: "Goal: plan a party. Outcome: cake", ts: "2026-01-02" });
    await store.addHistory({ id: "task:1", taskId: "1", title: "quarterly invoice export", summary: "Goal: export invoices again. Outcome: xlsx", ts: "2026-01-03" });
    const hits = await store.searchHistory("need the invoice export", 5);
    expect(hits.map((h) => h.id)).toEqual(["task:1"]);
    expect(hits[0]?.summary).toContain("xlsx");
    expect(await store.searchHistory("!!! ??? ...", 5)).toEqual([]);
  });

  it("builds a safe FTS query from punctuation-heavy text", () => {
    expect(ftsQuery('what is "the" status (of) invoice-42?')).toBe('"what" OR "the" OR "status" OR "invoice"');
    expect(ftsQuery("a b")).toBeNull();
  });
});

describe("R2 artifacts", () => {
  it("round-trips a file under its task prefix", async () => {
    const a = new R2Artifacts(env.ARTIFACTS);
    const key = await a.put("task-1", "out/report.txt", "hello");
    expect(key).toBe("tasks/task-1/out/report.txt");
    expect(new TextDecoder().decode((await a.get("task-1", "out/report.txt")) as ArrayBuffer)).toBe("hello");
    expect(await a.list("task-1")).toEqual(["out/report.txt"]);
    expect(await a.list("task-2")).toEqual([]);
  });

  it("rejects a file over the size cap and a name that escapes the prefix", async () => {
    const a = new R2Artifacts(env.ARTIFACTS, 10);
    await expect(a.put("t", "big.bin", "x".repeat(11))).rejects.toMatchObject({ code: "too_large" });
    await expect(a.put("t", "../escape", "x")).rejects.toBeInstanceOf(ArtifactError);
    await expect(a.put("t", "/abs", "x")).rejects.toMatchObject({ code: "bad_name" });
  });
});
