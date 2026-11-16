import { describe, expect, it } from "vitest";
import { Chat, readSse, writeDiff } from "../src/chat.ts";

const run = (events: Array<Record<string, any>>): Chat => {
  const c = new Chat();
  c.pushUser("do it");
  for (const e of events) c.apply(e);
  return c;
};

describe("web chat model", () => {
  it("streams thinking and the answer into single items and ends done", () => {
    const c = run([
      { type: "run", status: "running", task_id: "t1" },
      { type: "reasoning.delta", text: "Let me " },
      { type: "reasoning.delta", text: "think." },
      { type: "reasoning.done" },
      { type: "message.delta", text: "All " },
      { type: "message.delta", text: "done." },
      { type: "message.done", text: "All done." },
      { type: "run", status: "done" },
    ]);
    expect(c.items[1]).toEqual({ kind: "thinking", text: "Let me think.", done: true });
    expect(c.items[2]).toEqual({ kind: "assistant", text: "All done.", done: true });
    expect(c.busy).toBe(false);
    expect(c.taskId).toBe("t1");
  });

  it("builds one tool card from started, streamed args, finished and result, with a diff for writes", () => {
    const c = run([
      { type: "tool_call.started", call_id: "c1", name: "sandbox.write_file" },
      { type: "tool_call.args.delta", call_id: "c1", text: '{"path":"a.txt",' },
      { type: "tool_call.args.delta", call_id: "c1", text: '"content":"one\\ntwo"}' },
      { type: "tool_call.finished", call_id: "c1", name: "sandbox.write_file", args: '{"path":"a.txt","content":"one\\ntwo"}', risk: "write" },
      { type: "tool_call.result", call_id: "c1", status: "ok", output: "Wrote 7 bytes.", ms: 12 },
    ]);
    const tools = c.items.filter((i) => i.kind === "tool");
    expect(tools).toHaveLength(1);
    expect(tools[0]).toMatchObject({ status: "ok", risk: "write", output: "Wrote 7 bytes.", ms: 12 });
    expect(writeDiff((tools[0] as any).args)).toBe("+++ a.txt\n+ one\n+ two");
    expect(writeDiff("nope")).toBeNull();
    expect(writeDiff('{"command":"ls"}')).toBeNull();
  });

  it("finds the pending approval until it settles", () => {
    const c = run([{ type: "approval", approval_id: "a", kind: "tool", state: "requested", hash: "h", summary: "sandbox.exec ls" }, { type: "run", status: "waiting" }]);
    expect(c.busy).toBe(true);
    expect(c.pendingApproval?.hash).toBe("h");
    c.apply({ type: "approval", approval_id: "a", state: "approved", hash: "h" });
    expect(c.pendingApproval).toBeUndefined();
    expect(c.items.filter((i) => i.kind === "approval")).toHaveLength(1);
  });

  it("keeps one step line per step, the latest cost, and says plainly when a task fails", () => {
    const c = run([
      { type: "usage", step: 0, prompt: 10, completion: 5, reasoning: 2, cost: 0.001 },
      { type: "usage", step: 0, prompt: 10, completion: 6, reasoning: 2, cost: 0.002 },
      { type: "cost", task_total: 0.002, day_total: 0.01, task_cap: 1, day_cap: 5 },
      { type: "run", status: "failed", reason: "The daily cap is reached." },
    ]);
    expect(c.items.filter((i) => i.kind === "step")).toHaveLength(1);
    expect(c.cost).toEqual({ task: 0.002, day: 0.01, taskCap: 1, dayCap: 5 });
    expect(c.items.some((i) => i.kind === "note" && i.text === "Task failed: The daily cap is reached.")).toBe(true);
  });

  it("says Working until the model emits reasoning, and ignores events it does not know", () => {
    const c = run([{ type: "from_the_future" }]);
    expect(c.hasReasoning).toBe(false);
    c.apply({ type: "reasoning.delta", text: "x" });
    expect(c.hasReasoning).toBe(true);
    expect(c.items).toHaveLength(2);
  });
});

describe("readSse", () => {
  it("reads frames split across chunks and skips malformed ones", async () => {
    const enc = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(enc.encode('id: 1\ndata: {"type":"run","sta'));
        c.enqueue(enc.encode('tus":"running"}\n\ndata: not json\n\nid: 2\ndata: {"type":"note"}\n\n'));
        c.close();
      },
    });
    const seen: string[] = [];
    await readSse(new Response(body), (e) => seen.push(String(e.type)));
    expect(seen).toEqual(["run", "note"]);
  });
});
