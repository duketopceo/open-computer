import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { EffortCatalog } from "@/lib/api";
import { EffortPicker, type EffortChoice } from "./EffortPicker";

const CAT: EffortCatalog = {
  default: "low",
  max_budget: 1,
  models: ["small/a", "mid/b"],
  levels: [
    { id: "low", label: "Low", blurb: "Quick and cheap.", model: "small/a", fallbacks: ["small/x", "small/y"], budget: 0.1 },
    { id: "medium", label: "Medium", blurb: "Harder tasks.", model: "mid/b", budget: 0.3 },
  ],
};
const CHOICE: EffortChoice = { effort: "low", model: "", budget: "" };

describe("EffortPicker", () => {
  it("shows only the levels and a one-line budget until Advanced is opened", () => {
    render(<EffortPicker catalog={CAT} value={CHOICE} onChange={() => {}} />);
    expect(screen.getByText("up to $0.10 per run")).toBeInTheDocument();
    expect(screen.queryByText(/Quick and cheap/)).toBeNull();
    expect(screen.queryByText("small/x, small/y")).toBeNull();
    expect(screen.getByText("Low").closest("label")).toHaveAttribute("title", "Quick and cheap.");
  });

  it("puts the level's explainer, model and fallbacks inside Advanced", async () => {
    const user = userEvent.setup();
    render(<EffortPicker catalog={CAT} value={CHOICE} onChange={() => {}} />);
    await user.click(screen.getByRole("button", { name: "Advanced" }));
    expect(screen.getByText(/Quick and cheap/)).toBeInTheDocument();
    expect(screen.getByText("small/x, small/y")).toBeInTheDocument();
  });
});
