import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
import { MotionRoot, useMotionAllowed } from "@/lib/MotionRoot";
import type { Step } from "@/lib/runEvents";
import { StepCard } from "./StepCard";

const step: Step = { id: "s1", title: "Look it up", status: "done", firstTs: 1, firstSeq: 1, startedTs: 1, endedTs: 2 };

function Card({ slide = false }: { slide?: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <MotionRoot>
      <StepCard step={step} calls={[]} execs={[]} errors={[]} open={open} onToggle={() => setOpen((o) => !o)} now={null} slide={slide} />
    </MotionRoot>
  );
}

function reduce(on: boolean) {
  vi.stubGlobal("matchMedia", (q: string) => ({
    matches: on && q.includes("reduce"),
    media: q,
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
}

afterEach(() => {
  vi.unstubAllGlobals();
  document.documentElement.removeAttribute("data-effects");
});

describe("StepCard motion", () => {
  it("a new card starts hidden-and-lowered only when motion is allowed", () => {
    reduce(false);
    render(<Card slide />);
    expect(screen.getByTestId("step-s1").style.opacity).toBe("0");
  });

  it("with reduced motion a new card is fully visible at once", () => {
    reduce(true);
    render(<Card slide />);
    expect(screen.getByTestId("step-s1").style.opacity).not.toBe("0");
  });

  it("with Reduce effects a new card is fully visible at once", () => {
    reduce(false);
    document.documentElement.setAttribute("data-effects", "off");
    render(<Card slide />);
    expect(screen.getByTestId("step-s1").style.opacity).not.toBe("0");
  });

  it("follows Reduce effects switched while the page is open", async () => {
    reduce(false);
    function Probe() {
      return <span>{useMotionAllowed() ? "moving" : "still"}</span>;
    }
    render(<Probe />);
    expect(screen.getByText("moving")).toBeInTheDocument();
    await act(async () => document.documentElement.setAttribute("data-effects", "off"));
    expect(screen.getByText("still")).toBeInTheDocument();
    await act(async () => document.documentElement.removeAttribute("data-effects"));
    expect(screen.getByText("moving")).toBeInTheDocument();
  });

  it("opening a step shows its detail right away when motion is off", async () => {
    reduce(true);
    const user = userEvent.setup();
    render(<Card />);
    await user.click(screen.getByRole("button", { name: /Look it up/ }));
    expect(screen.getByText("Nothing recorded for this step yet.")).toBeVisible();
    expect(screen.getByRole("button", { name: /Look it up/ })).toHaveAttribute("aria-controls", "step-s1-detail");
    expect(document.getElementById("step-s1-detail")).not.toBeNull();
  });

  it("closing a step removes its detail when motion is off", async () => {
    reduce(true);
    const user = userEvent.setup();
    render(<Card />);
    const btn = screen.getByRole("button", { name: /Look it up/ });
    await user.click(btn);
    await user.click(btn);
    expect(screen.queryByText("Nothing recorded for this step yet.")).toBeNull();
  });
});
