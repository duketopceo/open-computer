import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  EVENT_TYPE_NAMES,
  EventSequencer,
  eventsJsonSchema,
  parseEvent,
  serializeEvent,
  type AgentEvent,
  type EventBody,
} from "../src/index.ts";

const OC_EVENTS_V1 = [
  "run", "step", "tool_call", "llm_call", "route", "approval", "cost", "source", "artifact", "change", "error",
  "memory_recall", "memory_write", "note",
];
const V1_1_ADDITIONS = [
  "message.delta", "message.done", "reasoning.delta", "reasoning.done", "tool_call.started", "tool_call.args.delta",
  "tool_call.finished", "tool_call.result", "usage",
];

const bodies: EventBody[] = [
  { type: "run", status: "running" },
  { type: "step", step: 0, state: "start" },
  { type: "message.delta", step: 0, text: "hi" },
  { type: "message.done", step: 0, text: "hi" },
  { type: "reasoning.delta", step: 0, kind: "summary", text: "thinking" },
  { type: "reasoning.done", step: 0, kind: "summary", tokens: 12 },
  { type: "tool_call.started", step: 0, call_id: "c1", name: "sandbox.exec", index: 0 },
  { type: "tool_call.args.delta", call_id: "c1", text: "{\"cmd\":" },
  { type: "tool_call.finished", call_id: "c1", name: "sandbox.exec", args: "{\"cmd\":\"ls\"}", risk: "exec" },
  { type: "tool_call.result", call_id: "c1", status: "ok", output: "a", ms: 4 },
  { type: "usage", step: 0, prompt: 10, completion: 5, reasoning: 2, cached: 0, cost: 0.001, estimated: false },
  { type: "cost", task_total: 0.1, day_total: 0.2, task_cap: 0.5, day_cap: null },
  { type: "approval", approval_id: "a1", kind: "tool", state: "requested", hash: "h", summary: "run ls" },
  { type: "error", code: "key_invalid", message: "The model key was rejected." },
  { type: "memory_recall", items: [{ id: "m1", title: "t", source: "task:1" }] },
  { type: "memory_write", id: "m2", tier: "core" },
  { type: "note", text: "n" },
];

describe("event contract", () => {
  it("round-trips every event type through serialize and parse", () => {
    const seq = new EventSequencer("task-1", 0, () => "2026-01-01T00:00:00.000Z");
    const seen = new Set<string>();
    for (const body of bodies) {
      const event = seq.stamp(body);
      const back = parseEvent(JSON.parse(serializeEvent(event)));
      expect(back).toEqual(event);
      seen.add(event.type);
    }
    expect([...seen].sort()).toEqual([...EVENT_TYPE_NAMES].sort());
  });

  it("assigns a monotonic sequence number starting where told", () => {
    const seq = new EventSequencer("t", 5);
    expect(seq.stamp({ type: "note", text: "a" }).seq).toBe(5);
    expect(seq.stamp({ type: "note", text: "b" }).seq).toBe(6);
    expect(seq.nextSeq).toBe(7);
  });

  it("rejects an unknown type with a named field", () => {
    const bad = { type: "bogus", task_id: "t", seq: 0, ts: "x" };
    expect(() => parseEvent(bad)).toThrowError(/type/);
  });

  it("rejects an event with a missing sequence number and names it", () => {
    const bad = { type: "note", task_id: "t", ts: "x", text: "a" } as unknown as AgentEvent;
    expect(() => parseEvent(bad)).toThrowError(/seq/);
  });

  it("keeps the checked-in JSON Schema equal to the generated one", () => {
    const path = fileURLToPath(new URL("../../../contracts/events.schema.json", import.meta.url));
    expect(readFileSync(path, "utf8")).toBe(JSON.stringify(eventsJsonSchema(), null, 2) + "\n");
  });

  it("only emits names from oc/events v1 or the documented v1.1 additions (tool_call is replaced by its stages)", () => {
    const allowed = new Set([...OC_EVENTS_V1, ...V1_1_ADDITIONS]);
    for (const name of EVENT_TYPE_NAMES) expect(allowed.has(name), name).toBe(true);
  });
});
