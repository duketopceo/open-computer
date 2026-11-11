// Guards the CSS side of "Reduce effects": every keyframe animation in index.css is declared inside the
// effects-on gate, so reduced motion or data-effects="off" leaves each element static and fully visible.
/// <reference types="vite/client" />
import { describe, expect, it } from "vitest";
import css from "../index.css?raw";

function gateBlock(): string {
  const start = css.indexOf("@media (prefers-reduced-motion: no-preference) {");
  expect(start).toBeGreaterThan(-1);
  let depth = 0;
  for (let i = css.indexOf("{", start); i < css.length; i++) {
    if (css[i] === "{") depth++;
    if (css[i] === "}" && --depth === 0) return css.slice(start, i + 1);
  }
  throw new Error("unbalanced gate block");
}

describe("index.css effects gate", () => {
  it("declares every `animation:` that names a keyframe only inside the effects-on gate", () => {
    const gate = gateBlock();
    const outside = css.replace(gate, "");
    const named = [...outside.matchAll(/animation:\s*([^;]+);/g)].map((m) => m[1].trim()).filter((v) => v !== "none");
    // The View Transition pseudo-elements set only duration/easing outside the gate; they never name a keyframe.
    expect(named).toEqual([]);
    expect(gate).toMatch(/html:not\(\[data-effects="off"\]\) \.fx-working::before/);
  });

  it("turns View Transitions off for Reduce effects and for reduced motion", () => {
    expect(css).toMatch(/html\[data-effects="off"\]::view-transition-old\(\*\)/);
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\) \{\s*::view-transition-group\(\*\)/);
  });

  it("keeps the slanted weave on the canvas: static, with a light variant, and no shader, sheen or drifting layer", () => {
    expect(css).toMatch(/body::before\s*\{[^}]*repeating-linear-gradient\(45deg[^}]*repeating-linear-gradient\(-45deg/s);
    expect(css).toMatch(/:root\[data-theme="light"\]\s*\{\s*--fx-weave:/);
    expect(css).toMatch(/prefers-color-scheme: light\)\s*\{\s*:root:not\(\[data-theme\]\)\s*\{\s*--fx-weave:/);
    const weave = css.slice(css.indexOf("body::before"));
    expect(weave.slice(0, weave.indexOf("}"))).not.toMatch(/animation/);
    expect(css).not.toMatch(/body::after/);
    expect(css).not.toMatch(/fx-sheen|data-ambient/);
  });

  it("has no decorative glow on the composer focus edge", () => {
    const focus = css.slice(css.indexOf(".fx-edge:focus-within"));
    expect(focus.slice(0, focus.indexOf("}"))).not.toMatch(/box-shadow:[^;]*,|box-shadow:\s*0 0 [2-9]\d*px|-\d+px\s+var/);
  });
});
