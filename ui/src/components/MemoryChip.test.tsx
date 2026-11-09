import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ThreadDetail } from "@/lib/api";

const forgetMemory = vi.fn();
vi.mock("@/lib/api", () => ({
  errorMessage: (e: unknown, fallback: string) => (e instanceof Error ? e.message : fallback),
  forgetMemory: (...args: unknown[]) => forgetMemory(...args),
}));

import { MemoryChip } from "./MemoryChip";
import { citations } from "./ThreadView";

const recall = {
  user_ts: 1,
  ts: 1.5,
  withheld: 1,
  atoms: [
    { id: "atom_1", title: "Deploy notes", source: "agent", score: 0.8 },
    { id: "atom_2", title: "Runner", source: "agent", score: 0.6, forgotten: true },
  ],
};

describe("MemoryChip", () => {
  beforeEach(() => {
    forgetMemory.mockReset(); // a block body: vitest would run a returned mock as teardown
  });

  it("names how many notes the reply used and lists their titles on open", async () => {
    render(<MemoryChip recall={recall} thread="th_00000001" />);
    const chip = screen.getByRole("button", { name: "Used 2 memories" });
    expect(chip).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(chip);
    expect(chip).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Deploy notes")).toBeInTheDocument();
    expect(screen.getByText("Forgotten")).toBeInTheDocument(); // atom_2 was forgotten earlier
    expect(screen.getByText(/1 note held back by the privacy check/)).toBeInTheDocument();
  });

  it("forgets one note for this thread and marks it", async () => {
    forgetMemory.mockResolvedValue({ ok: true, atom: "atom_1" });
    render(<MemoryChip recall={recall} thread="th_00000001" />);
    await userEvent.click(screen.getByRole("button", { name: "Used 2 memories" }));
    await userEvent.click(screen.getByRole("button", { name: "Forget Deploy notes" }));
    expect(forgetMemory).toHaveBeenCalledWith("atom_1", "th_00000001");
    await waitFor(() => expect(screen.getAllByText("Forgotten")).toHaveLength(2));
    expect(screen.queryByRole("button", { name: /^Forget / })).toBeNull();
  });

  it("keeps the note and says why when forget fails", async () => {
    forgetMemory.mockRejectedValue(new Error("console offline"));
    render(<MemoryChip recall={recall} thread="th_00000001" />);
    await userEvent.click(screen.getByRole("button", { name: "Used 2 memories" }));
    await userEvent.click(screen.getByRole("button", { name: "Forget Deploy notes" }));
    expect(await screen.findByText("console offline")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Forget Deploy notes" })).toBeEnabled();
  });

  it("renders nothing when the turn recalled nothing", () => {
    const { container } = render(<MemoryChip recall={{ ...recall, withheld: 0, atoms: [] }} thread="th_00000001" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("says when every hit was held back", () => {
    render(<MemoryChip recall={{ ...recall, withheld: 2, atoms: [] }} thread="th_00000001" />);
    expect(screen.getByRole("button", { name: "2 memories withheld" })).toBeInTheDocument();
  });
});

describe("citations", () => {
  const base = { id: "th_00000001", runs: {} } as unknown as ThreadDetail;
  it("puts each turn's chip on the first reply after the message it recalled for", () => {
    const t: ThreadDetail = {
      ...base,
      messages: [
        { role: "user", text: "a", ts: 1 },
        { role: "run", text: "a", ts: 2, run: "goal_1" },
        { role: "assistant", text: "done", ts: 3 },
        { role: "user", text: "b", ts: 4 },
        { role: "assistant", text: "ok", ts: 5 },
        { role: "user", text: "c", ts: 6 },
      ],
      memory: { recalls: [recall, { ...recall, user_ts: 6 }], writes: [] },
    };
    expect([...citations(t).keys()]).toEqual([1]); // message 4 had no recall; message 6 has no reply yet
  });
  it("is empty with memory off", () => {
    expect(citations({ ...base, messages: [{ role: "user", text: "a", ts: 1 }, { role: "assistant", text: "b", ts: 2 }] }).size).toBe(0);
  });
});
