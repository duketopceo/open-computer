/// <reference types="vite/client" />
import { describe, expect, it } from "vitest";
import css from "@tokens/oc-palette.css?raw";

// The Iris palette (services/opsview/static/oc-palette.css): one accent plus neutrals, every text pair at 4.5:1 or more.

function block(selector: string): Record<string, string> {
  const at = css.indexOf(selector);
  const body = css.slice(css.indexOf("{", at) + 1, css.indexOf("}", at));
  return Object.fromEntries([...body.matchAll(/--sl-([\w-]+):\s*(#[0-9a-f]{6})/gi)].map((m) => [m[1], m[2]]));
}

const lum = (hex: string) => {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((x) => (x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4));
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
};
const ratio = (a: string, b: string) => {
  const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
};

describe.each([
  ["dark", block(':root[data-theme="dark"]')],
  ["light", block(':root[data-theme="light"]')],
])("Iris palette, %s", (_name, t) => {
  it("defines one accent and the neutral scale", () => {
    for (const k of ["canvas", "surface-1", "surface-3", "ink", "ink-muted", "ink-subtle", "accent", "on-accent"]) expect(t[k], k).toBeTruthy();
  });

  it.each(["canvas", "surface-1", "surface-3"])("accent and inks read on %s", (bg) => {
    for (const fg of ["accent", "ink", "ink-muted", "ink-subtle"]) expect(ratio(t[fg], t[bg]), `${fg} on ${bg}`).toBeGreaterThanOrEqual(4.5);
  });

  it("the primary button's text reads on the accent", () => {
    expect(ratio(t["on-accent"], t.accent)).toBeGreaterThanOrEqual(4.5);
  });
});
