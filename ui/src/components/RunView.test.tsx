import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { RunDetail } from "@/lib/api";
import { RunView, plainProgress, spentNote } from "./RunView";

vi.mock("@/lib/runEvents", () => ({ hasStream: () => Promise.resolve(false) }));
vi.mock("./Artifacts", () => ({ Artifacts: () => null }));

const RUN = {
  run: "g_t1",
  goal: "say hi",
  origin: "cloud",
  declared: 2,
  started: 100,
  ended: 112,
  effort: { id: "quick", model: "google/gemini-2.5-flash", spent: 0.0004, budget: 0.05, paused: false },
  subtasks: [
    {
      subtask: "a",
      title: "step a",
      state: "DONE",
      engine: "openrouter",
      route: "cheap",
      placement: "cloud-openrouter",
      cost: 0.0003,
      started_ts: 100,
      finished_ts: 104,
      worker: { model: "deepseek/deepseek-v4-pro", provider: "DeepInfra", tokens_reasoning: 0 },
      router: { engine: "jev" },
    },
    { subtask: "b", title: "step b", state: "LEASED", route: "cheap", placement: "cloud-openrouter", cost: 0.0001, started_ts: 105 },
  ],
} as unknown as RunDetail;

describe("RunView in the chat (compact)", () => {
  it("shows state, plain progress, time, the model and a Details link, and none of the plumbing", () => {
    render(<RunView run={RUN} compact />);
    expect(screen.getByText(/1 of 2 steps done · 12s/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Details" })).toHaveAttribute("href", "#g_t1");
    expect(screen.getByText("deepseek-v4-pro")).toBeInTheDocument();
    const text = document.body.textContent ?? "";
    for (const noise of ["LEASED", "origin", "routed by", "effort quick", "$0.0003", "cloud-openrouter", "no reasoning returned"])
      expect(text, noise).not.toContain(noise);
    expect(within(screen.getByText("step b").closest("li")!).getByText("STARTING")).toBeInTheDocument();
  });
});

describe("RunView on the run page (full)", () => {
  it("keeps the detail: origin, effort, routed-by and per-step cost", () => {
    render(<RunView run={RUN} />);
    const text = document.body.textContent ?? "";
    for (const detail of ["origin cloud", "effort quick", "routed by", "$0.0003"]) expect(text, detail).toContain(detail);
    expect(text).not.toContain("LEASED");
  });
});

describe("plain wording", () => {
  it.each([
    [0, 0, "planning"],
    [0, 1, "0 of 1 step done"],
    [1, 3, "1 of 3 steps done"],
    [1, 1, "1 step done"],
    [3, 3, "all 3 steps done"],
  ])("%i of %i reads %s", (done, declared, want) => expect(plainProgress(done, declared)).toBe(want));

  it("shows spend only from a cent", () => {
    expect(spentNote([{ cost: 0.004 }, { cost: 0.005 }])).toBe("");
    expect(spentNote([{ cost: 0.006 }, { cost: 0.005 }])).toBe(" · $0.011");
  });
});

describe("a web step's sources in the chat card", () => {
  const sourced = {
    ...RUN,
    subtasks: [{ ...RUN.subtasks[0], result: "Cats are small.\n\nSources:\n[1] https://example.com/cats\n[2] https://docs.test/a" }],
  } as unknown as RunDetail;

  it("moves the list to the side panel and leaves a count", () => {
    render(<RunView run={sourced} compact />);
    expect(screen.getByText("Cats are small.")).toBeInTheDocument();
    expect(screen.getByText("Cited 2 sources, listed under Sources and tools.")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("https://example.com/cats");
  });

  it("keeps the list on the run page", () => {
    render(<RunView run={sourced} />);
    expect(document.body.textContent).toContain("[1] https://example.com/cats");
  });
});
