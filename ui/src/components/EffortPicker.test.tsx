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
  it("shows only the levels until Advanced is opened, with blurbs in the tooltip", () => {
    render(<EffortPicker catalog={CAT} value={CHOICE} onChange={() => {}} />);
    expect(screen.queryByText(/up to/)).toBeNull();
    expect(screen.queryByText(/per run/)).toBeNull();
    expect(screen.queryByText(/Quick and cheap/)).toBeNull();
    expect(screen.queryByText("small/x, small/y")).toBeNull();
    expect(screen.getByText("Low").closest("label")).toHaveAttribute("title", "Quick and cheap.");
    expect(screen.getByRole("radio", { name: /Low/ })).toBeChecked();
  });

  it("flags an invalid Advanced budget as an alert even while Advanced is closed", () => {
    render(<EffortPicker catalog={CAT} value={{ ...CHOICE, budget: "5" }} onChange={() => {}} />);
    expect(screen.getByRole("alert").textContent).toMatch(/above \$0 and up to \$1\.00/);
  });

  it("puts the model and budget controls inside Advanced, with the explainer only as a title", async () => {
    const user = userEvent.setup();
    render(<EffortPicker catalog={CAT} value={CHOICE} onChange={() => {}} />);
    await user.click(screen.getByRole("button", { name: "Advanced" }));
    expect(screen.getByLabelText("Model")).toBeInTheDocument();
    expect(screen.getByLabelText("Budget for this run, dollars")).toBeInTheDocument();
    expect(screen.queryByText(/Quick and cheap/)).toBeNull();
    expect(screen.queryByText("small/x, small/y")).toBeNull();
    expect(screen.getByLabelText("Model")).toHaveAttribute("title", expect.stringMatching(/Quick and cheap/));
    expect(screen.getByLabelText("Model").getAttribute("title")).toMatch(/small\/x, small\/y/);
  });
});
