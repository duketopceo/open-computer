import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const voiceStatus = vi.fn();
const getEffort = vi.fn();
const transcribe = vi.fn();

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    kind: string;
    status?: number;
    constructor(message: string, kind = "client", status?: number) {
      super(message);
      this.kind = kind;
      this.status = status;
    }
  },
  errorMessage: (e: unknown, fallback: string) => (e instanceof Error ? e.message : fallback),
  voiceStatus: (...args: unknown[]) => voiceStatus(...args),
  getEffort: (...args: unknown[]) => getEffort(...args),
  transcribe: (...args: unknown[]) => transcribe(...args),
}));

import { Composer } from "./Composer";

describe("Composer", () => {
  beforeEach(() => {
    voiceStatus.mockReset();
    getEffort.mockReset();
    transcribe.mockReset();
    getEffort.mockResolvedValue({
      default: "low",
      max_budget: 1,
      models: ["m"],
      levels: [
        { id: "low", label: "Low", blurb: "", model: "m", budget: 0.1 },
        { id: "medium", label: "Medium", blurb: "", model: "m", budget: 0.25 },
        { id: "high", label: "High", blurb: "", model: "m", budget: 0.5 },
      ],
    });
  });

  it("explains when dictation is not configured before opening the mic", async () => {
    voiceStatus.mockResolvedValue({ available: false, reason: "voice not configured" });
    const user = userEvent.setup();
    render(<Composer onSend={() => {}} showEffort={false} />);
    await user.click(screen.getByRole("button", { name: "Dictate" }));
    await waitFor(() => {
      expect(voiceStatus).toHaveBeenCalled();
      expect(screen.getByRole("alert").textContent).toMatch(/Dictation/i);
    });
    expect(transcribe).not.toHaveBeenCalled();
  });

  it("has no Search/Computer mode control and no attachment button", async () => {
    render(<Composer onSend={() => {}} showEffort />);
    expect(screen.queryByRole("radio", { name: /Search|Computer/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Attach|Attachments/ })).toBeNull();
  });
});
