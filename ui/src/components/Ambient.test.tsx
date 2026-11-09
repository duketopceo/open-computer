import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const startAmbient = vi.fn();
vi.mock("@/lib/ambient", () => ({ startAmbient: (...a: unknown[]) => startAmbient(...a) }));

import { Ambient } from "./Ambient";

function reduce(on: boolean) {
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: on && q.includes("reduce"), media: q, addEventListener: () => {}, removeEventListener: () => {} }));
}

afterEach(() => {
  vi.unstubAllGlobals();
  startAmbient.mockReset();
  document.documentElement.removeAttribute("data-effects");
  document.documentElement.removeAttribute("data-ambient");
});

describe("Ambient", () => {
  it("mounts nothing and loads nothing under reduced motion", () => {
    reduce(true);
    render(<Ambient />);
    expect(screen.queryByTestId("ambient")).toBeNull();
    expect(startAmbient).not.toHaveBeenCalled();
  });

  it("mounts nothing and loads nothing with Reduce effects on", () => {
    reduce(false);
    document.documentElement.setAttribute("data-effects", "off");
    render(<Ambient />);
    expect(screen.queryByTestId("ambient")).toBeNull();
    expect(startAmbient).not.toHaveBeenCalled();
  });

  it("starts the renderer and marks the page while it draws; unmount stops it", async () => {
    reduce(false);
    const stop = vi.fn();
    startAmbient.mockReturnValue(stop);
    const { unmount } = render(<Ambient />);
    await waitFor(() => expect(document.documentElement.getAttribute("data-ambient")).toBe("on"));
    expect(screen.getByTestId("ambient")).toHaveAttribute("aria-hidden", "true");
    unmount();
    expect(stop).toHaveBeenCalledOnce();
    expect(document.documentElement.hasAttribute("data-ambient")).toBe(false);
  });

  it("falls back to the weave for good when WebGL is unavailable", async () => {
    reduce(false);
    startAmbient.mockReturnValue(null);
    render(<Ambient />);
    await waitFor(() => expect(screen.queryByTestId("ambient")).toBeNull());
    expect(document.documentElement.hasAttribute("data-ambient")).toBe(false);
  });
});
