// Motion tokens and the one gate every animation in the SPA goes through.
//
// The spring presets are Rune's rune-motion tokens (github.com/anomalyco/rune, crates/rune-motion/src/tokens.rs,
// MIT, (c) Anomaly): `Spring::new(response, bounce)` maps onto a Motion spring's `visualDuration` and `bounce`.
// oc-scale.css carries the same response times as --motion-* durations for CSS-only motion.

import { flushSync } from "react-dom";

export interface SpringToken {
  type: "spring";
  /** Seconds until the spring visually settles (Rune's `response`). */
  visualDuration: number;
  /** 0 = no overshoot. */
  bounce: number;
}

const spring = (visualDuration: number, bounce: number): SpringToken => ({ type: "spring", visualDuration, bounce });

export const SPRING = {
  snap: spring(0.25, 0),
  settle: spring(0.3, 0),
  reflow: spring(0.4, 0),
  morph: spring(0.3, 0.16),
} as const;

const reducedByOs = (): boolean => {
  try {
    return typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
};

/** False when the OS asks for reduced motion or Settings "Reduce effects" is on. Read at call time, never cached. */
export function motionAllowed(root: HTMLElement = document.documentElement): boolean {
  return root.getAttribute("data-effects") !== "off" && !reducedByOs();
}

/** Run a state update inside a same-document View Transition when the browser has one and motion is allowed;
 *  otherwise apply it at once. The update is synchronous either way, so callers never wait on an animation. */
export function withViewTransition(update: () => void): void {
  const start = typeof document.startViewTransition === "function" ? document.startViewTransition.bind(document) : null;
  if (!start || !motionAllowed()) {
    update();
    return;
  }
  start(() => flushSync(update));
}
