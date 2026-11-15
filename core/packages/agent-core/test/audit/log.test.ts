import { describe, expect, it } from "vitest";
import {
  AuditLog,
  MemoryAuditStorage,
  createRedactor,
  exportNdjson,
  fromNdjson,
  verifyChain,
  type AgentEvent,
} from "../../src/index.ts";

function log(opts: { secrets?: string[]; captureContent?: boolean } = {}) {
  const storage = new MemoryAuditStorage();
  const audit = new AuditLog({
    storage,
    redactor: createRedactor(opts.secrets ?? []),
    now: () => 1_700_000_000_000,
    ...(opts.captureContent ? { captureContent: true } : {}),
  });
  return { storage, audit };
}

describe("audit log", () => {
  it("verifies a chain of 100 records and detects a change in record 50", async () => {
    const { storage, audit } = log();
    for (let i = 0; i < 100; i++) await audit.append("note", "t", { i });
    expect(await verifyChain(storage.records)).toEqual({ ok: true });
    const tampered = storage.records.map((r) => ({ ...r, data: { ...r.data } }));
    (tampered[50] as { data: Record<string, unknown> }).data.i = 999;
    expect(await verifyChain(tampered)).toMatchObject({ ok: false, at: 50 });
  });

  it("keeps one gap-free chain under concurrent appends", async () => {
    const { storage, audit } = log();
    await Promise.all(Array.from({ length: 50 }, (_, i) => audit.append("note", `t${i % 3}`, { i })));
    expect(storage.records.map((r) => r.seq)).toEqual(Array.from({ length: 50 }, (_, i) => i));
    expect(await verifyChain(storage.records)).toEqual({ ok: true });
  });

  it("exports then re-imports identical records with the same chain verdict", async () => {
    const { storage, audit } = log();
    for (let i = 0; i < 7; i++) await audit.append("note", "t", { i });
    const text = await exportNdjson(storage);
    const back = fromNdjson(text);
    expect(back).toEqual(storage.records);
    expect(await verifyChain(back)).toEqual({ ok: true });
  });

  it("stores a record with an Authorization header and a configured key value replaced", async () => {
    const key = "super-secret-provider-key-123";
    const { storage, audit } = log({ secrets: [key] });
    await audit.append("http", "t", {
      headers: { Authorization: `Bearer ${key}`, "x-trace": "ok" },
      note: `called with ${key} in text`,
    });
    const text = JSON.stringify(storage.records[0]);
    expect(text).not.toContain(key);
    expect(storage.records[0]?.data).toMatchObject({ headers: { Authorization: "[redacted]", "x-trace": "ok" } });
  });

  it("removes common key shapes even when they were never configured", () => {
    const r = createRedactor();
    const out = r.redact({ msg: "key sk-or-v1-abcdef1234567890 and Bearer abcdefghijkl1234" });
    expect(JSON.stringify(out)).not.toMatch(/sk-or-v1-abcdef|abcdefghijkl1234/);
  });

  it("records a usage event with tokens and cost, and no content field", async () => {
    const { storage, audit } = log();
    const ev: AgentEvent = { type: "usage", task_id: "t", seq: 3, ts: "x", step: 0, prompt: 10, completion: 5, reasoning: 1, cached: 0, cost: 0.002, estimated: false };
    await audit.recordEvent(ev);
    expect(storage.records[0]).toMatchObject({ kind: "event:usage", data: { prompt: 10, completion: 5, cost: 0.002 } });
    expect(Object.keys(storage.records[0]?.data ?? {})).not.toContain("text");
  });

  it("keeps message and tool-output text out by default and in when content capture is on", async () => {
    const done: AgentEvent = { type: "message.done", task_id: "t", seq: 1, ts: "x", step: 0, text: "private answer" };
    const off = log();
    await off.audit.recordEvent(done);
    expect(JSON.stringify(off.storage.records[0])).not.toContain("private answer");
    expect(off.storage.records[0]?.data).toMatchObject({ chars: 14 });
    const on = log({ captureContent: true });
    await on.audit.recordEvent(done);
    expect(JSON.stringify(on.storage.records[0])).toContain("private answer");
  });

  it("does not record token-by-token deltas", async () => {
    const { storage, audit } = log();
    expect(await audit.recordEvent({ type: "message.delta", task_id: "t", seq: 1, ts: "x", step: 0, text: "a" })).toBeNull();
    expect(storage.records).toHaveLength(0);
  });
});
