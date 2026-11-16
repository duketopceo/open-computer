import { introspectWorkflow, runInDurableObject } from "cloudflare:test";
import { beforeEach, describe, expect, it } from "vitest";
import { applySse, api, install, readSse, readSseUntil, seedSecrets, startTask, textTurn, toolTurn, waitFor } from "./support.ts";
import { env } from "./env.ts";
import defaults from "../../../contracts/defaults.json" with { type: "json" };
import { D1MemoryStore } from "../src/storage/d1-memory.ts";

beforeEach(async () => {
  await seedSecrets();
});

const session = (id: string) => env.SESSION.get(env.SESSION.idFromName(id));
const eventsUrl = (id: string) => `/v1/sessions/${id}/events`;
const pendingApproval = (id: string) => waitFor(async () => (await session(id).pendingApprovals())[0], "an approval request");
const decide = (sid: string, aid: string, body: object) => api(`/v1/sessions/${sid}/approvals/${aid}`, { method: "POST", body: JSON.stringify(body) });
const approvalRows = (id: string) =>
  runInDurableObject(session(id), (_i, state) => state.storage.sql.exec<{ id: string; state: string; step_key: string }>("SELECT id, state, step_key FROM approvals ORDER BY created_at").toArray());

async function withFastRetries(body: () => Promise<void>): Promise<void> {
  const intro = await introspectWorkflow(env.TASK_WORKFLOW);
  try {
    await intro.modifyAll(async (m) => {
      await m.disableRetryDelays();
    });
    await body();
  } finally {
    await intro.dispose();
  }
}

describe("a retried step is safe to repeat", () => {
  it("runs an approved tool once when the step is retried after a failure that came after the tool ran", async () => {
    const fx = install([toolTurn([{ id: "c1", name: "sandbox.exec", args: '{"command":"ls"}' }]), textTurn("listed")]);
    fx.failFlushAt.push(":2"); // the step that runs the approved tool loses its audit write once
    await withFastRetries(async () => {
      const { session_id, task_id } = await startTask({ goal: "list files" });
      const pending = await pendingApproval(session_id);
      expect((await decide(session_id, pending.id, { hash: pending.hash, approved: true })).status).toBe(200);
      const { events } = await readSse(await api(eventsUrl(session_id)));
      expect(fx.failFlushAt).toEqual([]); // the injected failure really happened
      expect(fx.sandboxes.get(task_id)?.execs).toEqual(["ls"]);
      expect(events.filter((e) => e.type === "tool_call.result")).toMatchObject([{ status: "ok" }]);
      expect(events.some((e) => e.type === "run" && e.status === "done")).toBe(true);
    });
  });

  it("expires the approval a failed attempt asked for, so a later human approval cannot wake it", async () => {
    const fx = install([toolTurn([{ id: "c1", name: "sandbox.exec", args: '{"command":"ls"}' }]), textTurn("listed")]);
    fx.failFlushAt.push(":1"); // the step that asks for approval fails after it has asked
    await withFastRetries(async () => {
      const { session_id } = await startTask({ goal: "list files" });
      const rows = await waitFor(async () => {
        const r = await approvalRows(session_id);
        return r.length >= 2 ? r : undefined;
      }, "the retried step to ask again");
      expect(rows.map((r) => r.state)).toEqual(["expired", "requested"]);
      const [stale, fresh] = rows;
      const hashOf = async (id: string) => (await session(session_id).pendingApprovals()).find((a) => a.id === id)?.hash ?? "x";
      const late = await decide(session_id, stale!.id, { hash: await hashOf(fresh!.id), approved: true });
      expect(late.status).toBe(409);
      const pending = await pendingApproval(session_id);
      expect(pending.id).toBe(fresh!.id);
      expect((await decide(session_id, pending.id, { hash: pending.hash, approved: true })).status).toBe(200);
      const { events } = await readSse(await api(eventsUrl(session_id)));
      expect(events.some((e) => e.type === "run" && e.status === "done")).toBe(true);
    });
  });

  it("does not replay a discarded attempt's text to a client that reconnects after the retry", async () => {
    let release!: () => void;
    const held = new Promise<void>((resolve) => (release = resolve));
    install([(call) => (call === 1 ? { partial: [{ t: "text", text: "par" }], throwAfter: new Error("connection reset"), waitFor: held } : textTurn("full answer"))]);
    await withFastRetries(async () => {
      const { session_id } = await startTask({ goal: "flaky" });
      // The client sees the first attempt's text, then drops its connection.
      const first = await readSseUntil(await api(eventsUrl(session_id)), (r) => r.events.some((e) => e.type === "message.delta" && e.text === "par"));
      expect(first.events.some((e) => e.type === "message.delta" && e.text === "par")).toBe(true);
      release();
      await waitFor(async () => {
        const t = await session(session_id).eventsAfter(-1);
        return t.some((e) => e.type === "run" && e.status === "done") ? true : undefined;
      }, "the retried task to finish");
      // It reconnects with its last id: it must be told the attempt it holds was replaced.
      const again = await api(eventsUrl(session_id), { headers: { "last-event-id": String(first.lastId) } });
      const merged = applySse({ events: [...first.events], superseded: [], lastId: first.lastId }, await again.text());
      expect(merged.superseded.length).toBeGreaterThanOrEqual(1);
      const deltas = merged.events.filter((e) => e.type === "message.delta").map((e) => (e as { text: string }).text);
      expect(deltas).toEqual(["full answer"]);
    });
  });

  it("starts a client that connects fresh after a retry from the new attempt only", async () => {
    install([(call) => (call === 1 ? { partial: [{ t: "text", text: "par" }], throwAfter: new Error("reset") } : textTurn("full answer"))]);
    await withFastRetries(async () => {
      const { session_id } = await startTask({ goal: "flaky" });
      await readSse(await api(eventsUrl(session_id)));
      const fresh = await readSse(await api(eventsUrl(session_id)));
      expect(fresh.events.filter((e) => e.type === "message.delta").map((e) => (e as { text: string }).text)).toEqual(["full answer"]);
    });
  });
});

describe("spend caps through the durable driver", () => {
  it("pauses for a cap raise, continues when it is approved, and stores the extra cap", async () => {
    install([textTurn("ok")]);
    const { session_id, task_id } = await startTask({ goal: "needs more budget", task_cap: 0.2 });
    const pending = await pendingApproval(session_id);
    expect(pending.kind).toBe("cap_raise");
    expect(pending.amount).toBeGreaterThan(0);
    await waitFor(async () => ((await session(session_id).taskState(task_id))?.status === "waiting" ? true : undefined), "the task to be saved as waiting");
    expect((await decide(session_id, pending.id, { hash: pending.hash, approved: true })).status).toBe(200);
    const { events } = await readSse(await api(eventsUrl(session_id)));
    expect(events.some((e) => e.type === "run" && e.status === "done")).toBe(true);
    const extra = await env.DB.prepare("SELECT amount FROM task_cap_extra WHERE task_id = ?").bind(task_id).first<{ amount: number }>();
    expect(extra?.amount).toBeCloseTo(pending.amount as number, 6);
  });

  it("ends the task when the cap raise is denied, without calling the model", async () => {
    const fx = install([textTurn("never")]);
    const { session_id } = await startTask({ goal: "denied budget", task_cap: 0.2 });
    const pending = await pendingApproval(session_id);
    await decide(session_id, pending.id, { hash: pending.hash, approved: false });
    const { events } = await readSse(await api(eventsUrl(session_id)));
    expect(events.at(-1)).toMatchObject({ type: "run", status: "failed", reason: expect.stringContaining("spend cap was not raised") });
    expect(fx.script.calls).toBe(0);
  });

  it("stops at the daily cap without asking, because a raise cannot lift a day total", async () => {
    const fx = install([textTurn("never")]);
    const { session_id } = await startTask({ goal: "day cap", task_cap: 5, day_cap: 0.2 });
    const { events } = await readSse(await api(eventsUrl(session_id)));
    expect(events.at(-1)).toMatchObject({ type: "run", status: "failed", reason: expect.stringContaining("daily spend cap") });
    expect(events.some((e) => e.type === "approval")).toBe(false);
    expect(fx.script.calls).toBe(0);
  });

  it("rejects a bad day cap with a plain message", async () => {
    install([textTurn("x")]);
    for (const day_cap of [0, -1, "lots"]) {
      const res = await api("/v1/tasks", { method: "POST", body: JSON.stringify({ goal: "g", day_cap }) });
      expect(res.status, String(day_cap)).toBe(400);
    }
  });
});

describe("memory toggle in the cloud core", () => {
  it("recalls and writes memory when it is on, and does neither when it is off", async () => {
    const goalWords = "zebra ledger quarterly reconciliation";
    await new D1MemoryStore(env.DB).addHistory({ id: "task:seed-zebra", taskId: "seed-zebra", title: goalWords, summary: `Goal: ${goalWords}`, ts: "2026-01-01" });
    const countRows = async () => (await env.DB.prepare("SELECT COUNT(*) AS n FROM memory_history").first<{ n: number }>())?.n ?? 0;

    install([textTurn("done")]);
    const on = await startTask({ goal: `${goalWords} again` });
    const onEvents = (await readSse(await api(eventsUrl(on.session_id)))).events;
    expect(onEvents.some((e) => e.type === "memory_recall")).toBe(true);
    expect(onEvents.some((e) => e.type === "memory_write")).toBe(true);
    const afterOn = await countRows();

    install([textTurn("done")]);
    const off = await startTask({ goal: `${goalWords} once more`, memory: false });
    const offEvents = (await readSse(await api(eventsUrl(off.session_id)))).events;
    expect(offEvents.some((e) => e.type === "memory_recall" || e.type === "memory_write")).toBe(false);
    expect(await countRows()).toBe(afterOn);
  });
});

describe("the default model", () => {
  it("comes from the shared defaults file when a task names none", async () => {
    const fx = install([textTurn("hi")]);
    const { session_id } = await startTask({ goal: "no model named" });
    await readSse(await api(eventsUrl(session_id)));
    expect(fx.script.requests[0]?.model).toBe(defaults.default_model);
    expect(defaults.default_model).toBe("z-ai/glm-5.3");
  });

  it("is overridden by a model the task names", async () => {
    const fx = install([textTurn("hi")]);
    const { session_id } = await startTask({ goal: "named model", model: "vendor/other-model" });
    await readSse(await api(eventsUrl(session_id)));
    expect(fx.script.requests[0]?.model).toBe("vendor/other-model");
  });
});
