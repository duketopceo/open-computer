import { describe, expect, it } from "vitest";
import { actionHash, ApprovalQueue, createPolicy, runLoop, type ApprovalRecord } from "../../src/index.ts";
import { echoTool, makeHarness, textTurn, toolTurn } from "../support/harness.ts";

async function waitPending(queue: ApprovalQueue): Promise<ApprovalRecord> {
  for (let i = 0; i < 500; i++) {
    const p = queue.pending()[0];
    if (p) return p;
    await new Promise((r) => setTimeout(r, 1));
  }
  throw new Error("no approval was requested");
}

describe("approval queue", () => {
  it("rejects a decision whose hash does not match the reviewed action", async () => {
    const q = new ApprovalQueue();
    const rec = await q.requester.request({ taskId: "t", kind: "tool", hash: "h1", summary: "s" });
    expect(q.decide(rec.id, { hash: "other", approved: true, actor: "human" })).toEqual({ ok: false, reason: "hash_mismatch" });
    expect(q.requester.get(rec.id)?.state).toBe("requested");
    q.dispose();
  });

  it("rejects a second decision on the same approval as already used", async () => {
    const q = new ApprovalQueue();
    const rec = await q.requester.request({ taskId: "t", kind: "tool", hash: "h1", summary: "s" });
    expect(q.decide(rec.id, { hash: "h1", approved: true, actor: "human" }).ok).toBe(true);
    expect(q.decide(rec.id, { hash: "h1", approved: true, actor: "human" })).toEqual({ ok: false, reason: "already_decided" });
    q.dispose();
  });

  it("rejects a decision after expiry", async () => {
    let t = 1000;
    const q = new ApprovalQueue({ ttlMs: 50, now: () => t });
    const rec = await q.requester.request({ taskId: "t", kind: "tool", hash: "h", summary: "s" });
    t += 51;
    expect(q.decide(rec.id, { hash: "h", approved: true, actor: "human" })).toEqual({ ok: false, reason: "expired" });
    expect(await q.requester.wait(rec.id)).toBe("expired");
    q.dispose();
  });

  it("gives the loop an object that cannot decide, at runtime as well as in types", () => {
    const q = new ApprovalQueue();
    expect("decide" in q.requester).toBe(false);
    expect(Object.keys(q.requester).sort()).toEqual(["get", "request", "wait"]);
    q.dispose();
  });
});

describe("approvals in the loop", () => {
  const askExec = createPolicy({ exec: "ask" });

  it("pauses at a gated tool, runs it after a matching human approval, and never before", async () => {
    let ran = false;
    const tool = echoTool("sh", "exec", () => ((ran = true), "done"));
    const h = makeHarness({
      turns: [toolTurn([{ id: "c1", name: "sh", args: '{"cmd":"ls"}' }]), textTurn("ok")],
      tools: [tool],
      policy: askExec,
    });
    const run = runLoop(h.state, h.deps);
    const pending = await waitPending(h.queue);
    expect(ran).toBe(false);
    const expected = await actionHash("sh", '{"cmd":"ls"}', "task-1");
    expect(pending.hash).toBe(expected);
    expect(h.queue.decide(pending.id, { hash: pending.hash, approved: true, actor: "human" }).ok).toBe(true);
    const { state, events } = await run;
    expect(ran).toBe(true);
    expect(state.status).toBe("done");
    expect(events.filter((e) => e.type === "approval").map((e) => (e as { state: string }).state)).toEqual(["requested", "approved"]);
    h.queue.dispose();
  });

  it("hands a denial to the model as an observation and the task continues", async () => {
    let ran = false;
    const tool = echoTool("sh", "exec", () => ((ran = true), "done"));
    const h = makeHarness({
      turns: [toolTurn([{ id: "c1", name: "sh", args: "{}" }]), textTurn("understood")],
      tools: [tool],
      policy: askExec,
    });
    const run = runLoop(h.state, h.deps);
    const pending = await waitPending(h.queue);
    h.queue.decide(pending.id, { hash: pending.hash, approved: false, actor: "human" });
    const { state } = await run;
    expect(ran).toBe(false);
    expect(state.status).toBe("done");
    expect(h.requests[1]?.messages.at(-1)).toEqual({ role: "tool", tool_call_id: "c1", content: "The user denied this action." });
    h.queue.dispose();
  });

  it("fails the task with the expiry reason when an approval expires", async () => {
    const tool = echoTool("sh", "exec");
    const h = makeHarness({ turns: [toolTurn([{ id: "c1", name: "sh", args: "{}" }])], tools: [tool], policy: askExec });
    h.queue.dispose();
    const short = new ApprovalQueue({ ttlMs: 20 });
    const run = runLoop(h.state, { ...h.deps, approvals: short.requester });
    const { state } = await run;
    expect(state.status).toBe("failed");
    expect(state.failureReason).toContain("expired");
    short.dispose();
  });

  it("does not count the wait for an approval against the wall-clock limit", async () => {
    let t = 1_000_000;
    const tool = echoTool("sh", "exec");
    const h = makeHarness({
      turns: [toolTurn([{ id: "c1", name: "sh", args: "{}" }]), textTurn("done after a long wait")],
      tools: [tool],
      policy: askExec,
      limits: { wallClockMs: 15 * 60 * 1000 },
      now: () => t,
    });
    const run = runLoop(h.state, h.deps);
    const pending = await waitPending(h.queue);
    t += 20 * 60 * 1000; // the human answers twenty minutes later
    h.queue.decide(pending.id, { hash: pending.hash, approved: true, actor: "human" });
    const { state } = await run;
    expect(state.status).toBe("done");
    h.queue.dispose();
  });

  it("still stops a task that runs past the limit while working", async () => {
    let t = 1_000_000;
    const h = makeHarness({
      turns: [toolTurn([{ id: "c1", name: "echo", args: "{}" }]), textTurn("never")],
      tools: [echoTool("echo", "read", () => ((t += 16 * 60 * 1000), "slow"))],
      limits: { wallClockMs: 15 * 60 * 1000 },
      now: () => t,
    });
    const { state } = await runLoop(h.state, h.deps);
    expect(state.status).toBe("failed");
    expect(state.failureReason).toContain("time limit");
  });

  it("blocks a tool the policy denies without asking", async () => {
    const tool = echoTool("rm", "write");
    const h = makeHarness({
      turns: [toolTurn([{ id: "c1", name: "rm", args: "{}" }]), textTurn("ok")],
      tools: [tool],
      policy: createPolicy({ write: "deny" }),
    });
    const { state } = await runLoop(h.state, h.deps);
    expect(state.status).toBe("done");
    expect(h.queue.pending()).toHaveLength(0);
    expect(h.requests[1]?.messages.at(-1)).toMatchObject({ content: "This action is blocked by policy." });
  });
});
