import { describe, expect, it } from "vitest";
import { CostGuard, MemoryLedger, estimateCost, runLoop, staticPriceBook, type ApprovalRecord, type ApprovalQueue } from "../../src/index.ts";
import { makeHarness, textTurn, usage } from "../support/harness.ts";

async function waitPending(queue: ApprovalQueue): Promise<ApprovalRecord> {
  for (let i = 0; i < 500; i++) {
    const p = queue.pending()[0];
    if (p) return p;
    await new Promise((r) => setTimeout(r, 1));
  }
  throw new Error("no approval was requested");
}

// 4096 default output tokens at this price estimate exactly five cents for an empty-ish prompt.
const FIVE_CENTS = { "example/model": { inPerMTok: 0, outPerMTok: 0.05 * 1_000_000 / 4096 } };

describe("spend guard", () => {
  it("Covers AE4: with a 0.50 cap and 0.48 spent, a 0.05 call pauses with a cap-raise approval and makes no provider call", async () => {
    const h = makeHarness({ turns: [textTurn("hello")], taskCap: 0.5, prices: FIVE_CENTS });
    await h.ledger.tryReserve({ taskId: "task-1", stepId: "earlier", day: "2026-01-01", amount: 0.48, taskCap: 9, dayCap: null });
    const run = runLoop(h.state, h.deps);
    const pending = await waitPending(h.queue);
    expect(pending.kind).toBe("cap_raise");
    expect(pending.amount).toBeCloseTo(0.03, 5);
    expect(h.requests).toHaveLength(0);
    h.queue.decide(pending.id, { hash: pending.hash, approved: true, actor: "human" });
    const { state } = await run;
    expect(state.status).toBe("done");
    expect(h.requests).toHaveLength(1);
    expect(await h.guard.taskCap("task-1")).toBeCloseTo(0.53, 5);
    h.queue.dispose();
  });

  it("denying the cap-raise ends the task as failed with the cap reason", async () => {
    const h = makeHarness({ turns: [textTurn("x")], taskCap: 0.5, prices: FIVE_CENTS });
    await h.ledger.tryReserve({ taskId: "task-1", stepId: "earlier", day: "2026-01-01", amount: 0.48, taskCap: 9, dayCap: null });
    const run = runLoop(h.state, h.deps);
    const pending = await waitPending(h.queue);
    h.queue.decide(pending.id, { hash: pending.hash, approved: false, actor: "human" });
    const { state } = await run;
    expect(state.status).toBe("failed");
    expect(state.failureReason).toContain("spend cap was not raised");
    expect(h.requests).toHaveLength(0);
    h.queue.dispose();
  });

  it("totals the day across two tasks and blocks the second when the day cap would be passed", async () => {
    const ledger = new MemoryLedger();
    const guard = new CostGuard(ledger, { taskCap: 10, dayCap: 0.1, now: () => Date.UTC(2026, 0, 1, 12) });
    expect((await guard.reserve("a", "a:0", 0.06)).ok).toBe(true);
    const second = await guard.reserve("b", "b:0", 0.06);
    expect(second).toMatchObject({ ok: false, scope: "day" });
    expect((await guard.totals("b")).day).toBeCloseTo(0.06, 5);
  });

  it("ignores the previous day when totalling", async () => {
    let now = Date.UTC(2026, 0, 1, 23);
    const guard = new CostGuard(new MemoryLedger(), { taskCap: 10, dayCap: 0.1, now: () => now });
    await guard.reserve("a", "a:0", 0.09);
    now = Date.UTC(2026, 0, 2, 1);
    expect((await guard.reserve("b", "b:0", 0.09)).ok).toBe(true);
  });

  it("reserves the conservative default for an unknown model price and flags it estimated", () => {
    const est = estimateCost({ model: "mystery/model", messages: [{ role: "user", content: "hi" }] }, staticPriceBook({}));
    expect(est.estimated).toBe(true);
    expect(est.usd).toBeGreaterThan(0.3); // 4096 output tokens at the conservative output price
  });

  it("replaces the reservation with the final usage cost and frees the difference", async () => {
    const h = makeHarness({ turns: [textTurn("hi", usage(0.001))], prices: FIVE_CENTS });
    const { events } = await runLoop(h.state, h.deps);
    const cost = events.find((e) => e.type === "cost");
    expect(cost).toMatchObject({ type: "cost" });
    expect((await h.guard.totals("task-1")).task).toBeCloseTo(0.001, 6);
  });

  it("keeps the reservation when the provider reports no cost, and marks the usage estimated", async () => {
    const h = makeHarness({ turns: [textTurn("hi", usage(null))], prices: FIVE_CENTS });
    const { events } = await runLoop(h.state, h.deps);
    expect(events.find((e) => e.type === "usage")).toMatchObject({ estimated: true, cost: null });
    expect((await h.guard.totals("task-1")).task).toBeCloseTo(0.05, 4);
  });

  it("re-reserving the same step replaces its amount instead of adding to it", async () => {
    const guard = new CostGuard(new MemoryLedger(), { taskCap: 1, dayCap: null });
    await guard.reserve("t", "t:0", 0.4);
    await guard.reserve("t", "t:0", 0.4);
    expect((await guard.totals("t")).task).toBeCloseTo(0.4, 6);
  });
});
