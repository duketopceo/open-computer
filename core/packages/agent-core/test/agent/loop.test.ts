import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { runLoop, type AgentEvent } from "../../src/index.ts";
import { echoTool, makeHarness, textTurn, toolTurn } from "../support/harness.ts";

const types = (events: AgentEvent[]) => events.map((e) => e.type);

describe("agent loop", () => {
  it("answers in one turn: step, text, run done in order", async () => {
    const h = makeHarness({ turns: [textTurn("hello")] });
    const { events, state } = await runLoop(h.state, h.deps);
    expect(types(events)).toEqual(["run", "step", "message.delta", "usage", "cost", "message.done", "step", "run"]);
    expect(events.at(-1)).toMatchObject({ type: "run", status: "done" });
    expect(state.status).toBe("done");
  });

  it("round-trips a tool call: assistant call message, tool result, replay records, then a final answer", async () => {
    const h = makeHarness({
      turns: [toolTurn([{ id: "c1", name: "echo", args: '{"x":1}' }]), textTurn("finished")],
      tools: [echoTool()],
    });
    const { state, events } = await runLoop(h.state, h.deps);
    expect(state.status).toBe("done");
    expect(h.requests).toHaveLength(2);
    const second = h.requests[1]?.messages ?? [];
    const assistant = second.find((m) => m.role === "assistant");
    expect(assistant).toMatchObject({
      role: "assistant",
      tool_calls: [{ id: "c1", name: "echo", arguments: '{"x":1}' }],
      reasoning_details: [{ type: "reasoning.encrypted", data: "REPLAY", index: 0 }],
    });
    expect(second.at(-1)).toEqual({ role: "tool", tool_call_id: "c1", content: 'ran echo {"x":1}' });
    expect(events.filter((e) => e.type === "tool_call.result")).toHaveLength(1);
  });

  it("executes parallel tool calls in call order and reports results in that order", async () => {
    const order: string[] = [];
    const slow = echoTool("slow", "read", () => (order.push("slow"), "s"));
    const fast = echoTool("fast", "read", () => (order.push("fast"), "f"));
    const h = makeHarness({
      turns: [
        toolTurn([
          { id: "a", name: "slow", args: "{}" },
          { id: "b", name: "fast", args: "{}" },
        ]),
        textTurn("ok"),
      ],
      tools: [slow, fast],
    });
    const { events } = await runLoop(h.state, h.deps);
    expect(order).toEqual(["slow", "fast"]);
    const results = events.filter((e) => e.type === "tool_call.result").map((e) => (e as { call_id: string }).call_id);
    expect(results).toEqual(["a", "b"]);
  });

  it("returns an error observation for an unknown tool and keeps going", async () => {
    const h = makeHarness({ turns: [toolTurn([{ id: "c1", name: "nope", args: "{}" }]), textTurn("recovered")] });
    const { state } = await runLoop(h.state, h.deps);
    expect(state.status).toBe("done");
    const tool = h.requests[1]?.messages.at(-1);
    expect(tool).toMatchObject({ role: "tool", content: expect.stringContaining('Unknown tool "nope"') });
  });

  it("ends the run as failed with the cap named when the step limit is reached", async () => {
    const h = makeHarness({
      turns: [toolTurn([{ id: "c", name: "echo", args: "{}" }])],
      tools: [echoTool()],
      limits: { maxSteps: 2 },
    });
    const { state, events } = await runLoop(h.state, h.deps);
    expect(state.status).toBe("failed");
    expect(events.at(-1)).toMatchObject({ type: "run", status: "failed", reason: expect.stringContaining("step limit of 2") });
  });

  it("ends the run as failed after three consecutive tool failures", async () => {
    const boom = echoTool("boom", "read", () => {
      throw new Error("nope");
    });
    const h = makeHarness({
      turns: [
        toolTurn([
          { id: "1", name: "boom", args: "{}" },
          { id: "2", name: "boom", args: "{}" },
          { id: "3", name: "boom", args: "{}" },
        ]),
      ],
      tools: [boom],
    });
    const { state } = await runLoop(h.state, h.deps);
    expect(state.status).toBe("failed");
    expect(state.failureReason).toContain("3 times in a row");
  });

  it("ends the run as cancelled when aborted during a model stream and keeps events already emitted", async () => {
    const ac = new AbortController();
    const h = makeHarness({ turns: [textTurn("a long answer")], signal: ac.signal });
    const { events, state } = await runLoop(h.state, h.deps, {
      onEvent: (e) => {
        if (e.type === "message.delta") ac.abort();
      },
    });
    expect(state.status).toBe("cancelled");
    expect(types(events)).toContain("message.delta");
    expect(events.at(-1)).toMatchObject({ type: "run", status: "cancelled" });
  });

  it("runs a tool once however often the step is retried: a journaled call is replayed, not executed again", async () => {
    let runs = 0;
    const tool = echoTool("echo", "read", () => ((runs += 1), "side effect"));
    const store = new Map<string, { status: "ok" | "error"; output: string; ms: number }>();
    const journal = { get: (k: string) => store.get(k), put: (k: string, o: { status: "ok" | "error"; output: string; ms: number }) => void store.set(k, o) };
    const script = [toolTurn([{ id: "c1", name: "echo", args: "{}" }]), textTurn("done")];
    const first = makeHarness({ turns: script, tools: [tool] });
    await runLoop(first.state, { ...first.deps, journal });
    expect(runs).toBe(1);
    expect([...store.keys()]).toEqual(["task-1:1:c1"]);
    // The same task again from its start (a retried step reloading old state): the call is replayed from the journal.
    const retry = makeHarness({ turns: script, tools: [tool] });
    const { events, state } = await runLoop(retry.state, { ...retry.deps, journal });
    expect(runs).toBe(1);
    expect(state.status).toBe("done");
    expect(events.find((e) => e.type === "tool_call.result")).toMatchObject({ status: "ok", output: "side effect" });
  });

  it("journals a failed tool call too, so a retry does not run it again", async () => {
    let runs = 0;
    const boom = echoTool("boom", "read", () => {
      runs += 1;
      throw new Error("failed once");
    });
    const store = new Map<string, { status: "ok" | "error"; output: string; ms: number }>();
    const journal = { get: (k: string) => store.get(k), put: (k: string, o: { status: "ok" | "error"; output: string; ms: number }) => void store.set(k, o) };
    const turns = [toolTurn([{ id: "c1", name: "boom", args: "{}" }]), textTurn("ok")];
    const a = makeHarness({ turns, tools: [boom] });
    await runLoop(a.state, { ...a.deps, journal });
    const b = makeHarness({ turns, tools: [boom] });
    await runLoop(b.state, { ...b.deps, journal });
    expect(runs).toBe(1);
  });

  it("matches the stored golden event sequence for a fixed script", async () => {
    const h = makeHarness({
      turns: [toolTurn([{ id: "c1", name: "echo", args: '{"x":1}' }]), textTurn("done")],
      tools: [echoTool()],
    });
    const { events } = await runLoop(h.state, h.deps);
    const actual = JSON.stringify(events.map(({ ts: _ts, ...rest }) => rest), null, 2) + "\n";
    const path = fileURLToPath(new URL("../fixtures/golden/tool-roundtrip.events.json", import.meta.url));
    if (process.env.UPDATE_GOLDEN === "1") writeFileSync(path, actual);
    expect(actual).toBe(readFileSync(path, "utf8"));
  });
});
