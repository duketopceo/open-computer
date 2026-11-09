import { describe, expect, it } from "vitest";
import type { RunDetail } from "./api";
import { nowDoing } from "./nowDoing";

const run = (over: Omit<Partial<RunDetail>, "subtasks"> & { subtasks?: unknown[] }) =>
  ({ run: "g", goal: "g", origin: "cloud", device_id: null, declared: 2, worker_failures: [], started: 100, subtasks: [], ...over }) as unknown as RunDetail;
const step = (subtask: string, state: string, more: object = {}) => ({ subtask, title: `step ${subtask}`, state, ...more });

describe("nowDoing", () => {
  it("names the running step and when it started", () => {
    expect(nowDoing(run({ subtasks: [step("a", "DONE"), step("b", "RUNNING", { started_ts: 105 })] }))).toEqual({ text: "step b", since: 105 });
  });
  it("counts parallel steps and treats a leased step as running", () => {
    expect(nowDoing(run({ subtasks: [step("a", "LEASED", { started_ts: 1 }), step("b", "RUNNING")] }))?.text).toBe("step a (+1 more)");
  });
  it("puts the owner first when a step awaits approval", () => {
    expect(nowDoing(run({ subtasks: [step("a", "RUNNING"), step("b", "AWAITING-APPROVAL")] }))).toEqual({
      text: "Waiting for your approval: step b",
      needsYou: true,
    });
  });
  it("says a backing-off step is waiting and retrying", () => {
    const w = { kind: "sandbox", attempt: 0, max: 3, since: 110 };
    expect(nowDoing(run({ subtasks: [step("a", "QUEUED", { waiting_on: w })] }))?.text).toMatch(/^step a: waiting for .* attempt 1 of 3$/);
  });
  it("plans, queues and wraps up in plain words", () => {
    expect(nowDoing(run({}))?.text).toBe("Planning the steps");
    expect(nowDoing(run({ subtasks: [step("a", "DONE"), step("b", "QUEUED")] }))?.text).toBe("Up next: step b");
    expect(nowDoing(run({ subtasks: [step("a", "DONE")] }))?.text).toBe("Wrapping up");
  });
  it("is quiet once the run ended, failed, paused or waits on its plan", () => {
    const busy = [step("a", "RUNNING")];
    expect(nowDoing(run({ ended: 120, subtasks: busy }))).toBeNull();
    expect(nowDoing(run({ error: "boom", subtasks: busy }))).toBeNull();
    expect(nowDoing(run({ plan: { state: "planned" } as RunDetail["plan"], subtasks: busy }))).toBeNull();
    expect(nowDoing(run({ effort: { paused: true } as RunDetail["effort"], subtasks: busy }))).toBeNull();
  });
});
