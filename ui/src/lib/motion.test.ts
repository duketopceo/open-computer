import { afterEach, describe, expect, it, vi } from "vitest";
import { SPRING, motionAllowed, withViewTransition } from "./motion";

type StartVT = (cb: () => void) => unknown;
const doc = document as unknown as { startViewTransition?: StartVT };

function reduce(on: boolean) {
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: on && q.includes("reduce"), media: q }));
}

afterEach(() => {
  vi.unstubAllGlobals();
  delete doc.startViewTransition;
  document.documentElement.removeAttribute("data-effects");
});

describe("motion gate", () => {
  it("allows motion by default and refuses it for reduced motion or Reduce effects", () => {
    reduce(false);
    expect(motionAllowed()).toBe(true);
    document.documentElement.setAttribute("data-effects", "off");
    expect(motionAllowed()).toBe(false);
    document.documentElement.removeAttribute("data-effects");
    reduce(true);
    expect(motionAllowed()).toBe(false);
  });

  it("keeps Rune's spring presets", () => {
    expect(SPRING.snap).toEqual({ type: "spring", visualDuration: 0.25, bounce: 0 });
    expect(SPRING.morph.bounce).toBeCloseTo(0.16);
  });
});

describe("withViewTransition", () => {
  it("wraps the update in a view transition when allowed", () => {
    reduce(false);
    const start = vi.fn((cb: () => void) => cb());
    doc.startViewTransition = start;
    const update = vi.fn();
    withViewTransition(update);
    expect(start).toHaveBeenCalledOnce();
    expect(update).toHaveBeenCalledOnce();
  });

  it("applies the update directly when effects are off or motion is reduced", () => {
    const start = vi.fn((cb: () => void) => cb());
    doc.startViewTransition = start;
    const update = vi.fn();
    reduce(true);
    withViewTransition(update);
    reduce(false);
    document.documentElement.setAttribute("data-effects", "off");
    withViewTransition(update);
    expect(start).not.toHaveBeenCalled();
    expect(update).toHaveBeenCalledTimes(2);
  });

  it("applies the update directly when the browser has no View Transitions", () => {
    reduce(false);
    const update = vi.fn();
    withViewTransition(update);
    expect(update).toHaveBeenCalledOnce();
  });
});
