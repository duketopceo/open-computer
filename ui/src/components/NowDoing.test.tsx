import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { NowDoing } from "./NowDoing";

describe("NowDoing", () => {
  it("shows one condensed line and folds the full stream behind Follow", () => {
    render(
      <NowDoing now={{ text: "Fetching the page", since: Date.now() / 1000 - 3 }}>
        <p>full stream</p>
      </NowDoing>,
    );
    const btn = screen.getByRole("button", { name: /Now: Fetching the page/ });
    expect(btn).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("full stream")).toBeNull();
    fireEvent.click(btn);
    expect(btn).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("full stream")).toBeVisible();
    expect(screen.getByText("Hide")).toBeInTheDocument();
  });

  it("drops the Now: prefix when the owner is the blocker", () => {
    render(
      <NowDoing now={{ text: "Waiting for your approval: push", needsYou: true }}>
        <p>x</p>
      </NowDoing>,
    );
    expect(screen.getByRole("button").textContent).not.toContain("Now:");
  });
});
