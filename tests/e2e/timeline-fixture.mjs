// Event-stream fixtures for the run timeline (contract: docs/design/event-stream.md plus the
// route / llm_call / shell.exec / waiting_on extensions). Shared by spa.e2e.mjs and shots.mjs.
export const GID = "goal_0123abcd";

export function stream(goal = GID) {
  let seq = 0;
  let ts = Math.floor(Date.now() / 1000) - 60; // recent, so live elapsed times read sensibly
  const events = [];
  const add = (type, data, dt = 1) => {
    ts += dt;
    const e = { id: `${goal}:${++seq}`, v: 1, goal, seq, ts, type, data };
    events.push(e);
    return e;
  };
  return { events, add, ts: () => ts, seq: () => seq };
}

/** Three steps: one done with a router fallback, one failed command (rc 1), one waiting on the sandbox. */
export function threeSteps(goal = GID) {
  const s = stream(goal);
  s.add("run", { status: "running", title: "Summarise the sales sheet", cap_usd: 0.5 });
  s.add("llm_call", { id: "call_plan", kind: "plan", status: "ok", detail: '{"steps":["fetch","parse","report"]}' });
  s.add("step", { id: "step_t1", title: "Fetch the sheet", status: "running" });
  s.add("step", { id: "step_t2", title: "Parse the rows", status: "pending" });
  s.add("step", { id: "step_t3", title: "Write the report", status: "pending" });
  s.add("route", { id: "route_t1", step: "step_t1", engine: "jev", model: "deepseek-v4-pro", confidence: 0.82, ms: 640, fallbacks: [{ engine: "decider", kind: "decider_unavailable", status: 404, reason: "No endpoints found" }] });
  s.add("llm_call", { id: "call_w1", step: "step_t1", kind: "work", model_requested: "deepseek-v4-pro", model_served: "deepseek-v4-pro", provider: "acme", tokens_in: 1200, tokens_out: 340, tokens_reasoning: 80, cost_usd: 0.0103, latency_ms: 5200, reasoning: "Download the CSV then count rows." });
  s.add("tool_call", { id: "tool_t1_1", name: "shell.exec", step: "step_t1", status: "running", command: "curl -s http://127.0.0.1/sheet.csv | wc -l" });
  s.add("tool_call", { id: "tool_t1_1", name: "shell.exec", step: "step_t1", status: "ok", rc: 0, stdout_tail: ["42"], stderr_tail: [], duration_ms: 310 });
  s.add("step", { id: "step_t1", title: "Fetch the sheet", status: "done", model: "deepseek-v4-pro" }, 4);
  s.add("step", { id: "step_t2", title: "Parse the rows", status: "running" });
  s.add("llm_call", { id: "call_w2", step: "step_t2", kind: "work", model_requested: "deepseek-v4-pro", model_served: "deepseek-v4-pro", provider: "acme", tokens_in: 800, tokens_out: 120, tokens_reasoning: 0, cost_usd: 0.004, latency_ms: 1900 });
  s.add("tool_call", { id: "tool_t2_1", name: "shell.exec", step: "step_t2", status: "running", command: "python3 parse.py" });
  s.add("tool_call", { id: "tool_t2_1", name: "shell.exec", step: "step_t2", status: "error", rc: 1, stdout_tail: [], stderr_tail: ["Traceback (most recent call last):", '  File "parse.py", line 3, in <module>', "IndexError: list index out of range"], duration_ms: 220 });
  s.add("error", { id: "err_t2", step: "step_t2", code: "CommandExitException", message: "rc=1: IndexError: list index out of range" });
  s.add("step", { id: "step_t2", title: "Parse the rows", status: "failed" });
  s.add("step", { id: "step_t3", title: "Write the report", status: "running", waiting_on: { kind: "sandbox_boot", attempt: 1, max: 4, since: s.ts() - 12, detail: "sandbox backend refused to boot" } });
  s.add("cost", { spent_usd: 0.0164, cap_usd: 0.5 });
  return s;
}

/** n steps, all done, in one reply (the API caps a reply at 500). */
export function manySteps(n, goal = GID) {
  const s = stream(goal);
  s.add("run", { status: "done", title: "A very long run" });
  for (let i = 1; i <= n; i++) s.add("step", { id: `step_s${i}`, title: `Step ${i}`, status: "done", model: "deepseek-v4-pro" }, 0.1);
  return s;
}
