/// <reference types="vite/client" />
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { NAV } from "@/lib/nav";
import { Icon } from "./Icon";
import { ICONS, ICON_NAMES } from "./paths";
import light from "../../../../assets/brand/icons.svg?raw";
import dark from "../../../../assets/brand/icons-dark.svg?raw";
import { renderSheet } from "../../../scripts/icon-sheet.mjs";

describe("icon set", () => {
  it("draws every icon on the 16 grid: square caps, 1.5 stroke, currentColor, a non-empty path", () => {
    for (const name of ICON_NAMES) {
      const { container, unmount } = render(<Icon name={name} />);
      const svg = container.querySelector("svg")!;
      expect(svg.getAttribute("viewBox")).toBe("0 0 16 16");
      expect(svg.getAttribute("stroke")).toBe("currentColor");
      expect(svg.getAttribute("stroke-width")).toBe("1.5");
      expect(svg.getAttribute("stroke-linecap")).toBe("square");
      expect(container.querySelectorAll("path").length).toBeGreaterThan(0);
      unmount();
    }
  });

  it("keeps every coordinate inside the 16 grid", () => {
    for (const name of ICON_NAMES) {
      for (const seg of ICONS[name]) {
        const nums = seg.d.match(/-?\d+(\.\d+)?/g)!.map(Number);
        // relative commands (h v l lower case) carry deltas, so check the absolute pass below via a walk
        expect(nums.length).toBeGreaterThan(0);
        let x = 0, y = 0;
        const toks = seg.d.match(/[MmLlHhVvZz]|-?\d+(\.\d+)?/g)!;
        let cmd = "";
        for (let i = 0; i < toks.length; ) {
          if (/[A-Za-z]/.test(toks[i])) cmd = toks[i++];
          if (cmd === "Z" || cmd === "z") continue;
          const take = (n: number) => toks.slice(i, (i += n)).map(Number);
          if (cmd === "M" || cmd === "L") [x, y] = take(2);
          else if (cmd === "m" || cmd === "l") { const [a, b] = take(2); x += a; y += b; }
          else if (cmd === "H") [x] = take(1);
          else if (cmd === "h") x += take(1)[0];
          else if (cmd === "V") [y] = take(1);
          else if (cmd === "v") y += take(1)[0];
          expect(x, `${name} ${seg.d}`).toBeGreaterThanOrEqual(0);
          expect(x, `${name} ${seg.d}`).toBeLessThanOrEqual(16);
          expect(y, `${name} ${seg.d}`).toBeGreaterThanOrEqual(0);
          expect(y, `${name} ${seg.d}`).toBeLessThanOrEqual(16);
          if (cmd === "M") cmd = "L";
          if (cmd === "m") cmd = "l";
        }
      }
    }
  });

  it("is decorative without a title and named with one", () => {
    const a = render(<Icon name="send" />).container.querySelector("svg")!;
    expect(a.getAttribute("aria-hidden")).toBe("true");
    const b = render(<Icon name="send" title="Send message" />).getByRole("img", { name: "Send message" });
    expect(b).toBeTruthy();
  });

  it("gives every nav entry an existing icon", () => {
    for (const n of NAV) expect(ICON_NAMES).toContain(n.icon);
  });

  it("matches the committed sheets in assets/brand", () => {
    expect(light).toBe(renderSheet(false));
    expect(dark).toBe(renderSheet(true));
  });
});
