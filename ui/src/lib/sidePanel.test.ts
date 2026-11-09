import { describe, expect, it } from "vitest";
import type { ThreadDetail } from "./api";
import { applyEvents, emptyTimeline, type RunEvent } from "./runEvents";
import { activity, callLine, splitSources, threadSources } from "./sidePanel";

const ANSWER =
  "Cats are small.\n\nSources:\n[1] https://www.example.com/cats\n[2] https://docs.test/a?b=1";

describe("splitSources", () => {
  it("separates the appended list from the answer", () => {
    expect(splitSources(ANSWER)).toEqual({
      body: "Cats are small.",
      urls: ["https://www.example.com/cats", "https://docs.test/a?b=1"],
    });
  });
  it("leaves text without a list alone, including a 'Sources:' mid-answer", () => {
    expect(splitSources("plain")).toEqual({ body: "plain", urls: [] });
    const mid = "Sources:\n[1] https://a.test\n\nand more text";
    expect(splitSources(mid).urls).toEqual([]);
  });
  it("drops non-web entries", () => {
    expect(
      splitSources(
        "x\n\nSources:\n[1] javascript:alert(1)\n[2] https://ok.test",
      ).urls,
    ).toEqual(["https://ok.test"]);
  });
});

describe("threadSources", () => {
  it("numbers each URL once across runs and remembers which steps cited it", () => {
    const thread = {
      runs: {
        g1: {
          subtasks: [
            {
              subtask: "a",
              title: "Look up cats",
              state: "DONE",
              result: ANSWER,
            },
          ],
        },
        g2: {
          subtasks: [
            {
              subtask: "b",
              title: "Check again",
              state: "DONE",
              result:
                "Yes.\n\nSources:\n[1] https://docs.test/a?b=1\n[2] https://new.test/",
            },
            {
              subtask: "c",
              title: "Failed",
              state: "FAILED",
              result: "x\n\nSources:\n[1] https://skip.test",
            },
          ],
        },
        gone: null,
      },
    } as unknown as ThreadDetail;
    expect(threadSources(thread)).toEqual([
      {
        n: 1,
        url: "https://www.example.com/cats",
        host: "example.com",
        steps: ["Look up cats"],
      },
      {
        n: 2,
        url: "https://docs.test/a?b=1",
        host: "docs.test",
        steps: ["Look up cats", "Check again"],
      },
      {
        n: 3,
        url: "https://new.test/",
        host: "new.test",
        steps: ["Check again"],
      },
    ]);
  });
});

describe("activity", () => {
  const ev = (seq: number, type: string, data: object): RunEvent => ({
    id: `g:${seq}`,
    v: 1,
    goal: "g",
    seq,
    ts: seq,
    type,
    data: data as Record<string, unknown>,
  });
  const tl = applyEvents(emptyTimeline(), [
    ev(1, "step", { id: "step_t1", title: "Research", status: "running" }),
    ev(2, "tool_call", {
      id: "tool_1",
      name: "web.search",
      status: "running",
      step: "step_t1",
      summary: "cats",
    }),
    ev(3, "tool_call", {
      id: "tool_1",
      name: "web.search",
      status: "ok",
      step: "step_t1",
      summary: "cats",
      duration_ms: 900,
    }),
    ev(4, "tool_call", {
      id: "tool_2",
      name: "web.fetch",
      status: "error",
      step: "step_t1",
      summary: "https://x.test/p",
      destination: "x.test",
    }),
    ev(5, "tool_call", {
      id: "tool_3",
      name: "shell.exec",
      status: "ok",
      step: "step_t1",
      command: "\nls -la\npwd",
      rc: 0,
    }),
  ]);

  it("splits web searches from other tool calls, one row per call id, with the step's title", () => {
    const { web, tools } = activity("g", tl);
    expect(web.map((a) => [a.call.name, a.call.status, a.step])).toEqual([
      ["web.search", "ok", "Research"],
      ["web.fetch", "error", "Research"],
    ]);
    expect(tools.map((a) => a.call.name)).toEqual(["shell.exec"]);
  });

  it("says each call in plain words", () => {
    const { web, tools } = activity("g", tl);
    expect(web.map((a) => callLine(a.call))).toEqual([
      "Searched “cats”",
      "Read x.test",
    ]);
    expect(callLine(tools[0].call)).toBe("ls -la");
  });
});
