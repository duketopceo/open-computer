import { describe, expect, it } from "vitest";
import config from "../../tailwind.config";

describe("type scale", () => {
  it("is exactly the DESIGN.md scale 12 / 13 / 14 / 16 / 20 / 28", () => {
    const sizes = Object.values(config.theme!.fontSize as Record<string, [string, string]>).map((v) => v[0]);
    expect(sizes).toEqual(["12px", "13px", "14px", "16px", "20px", "28px"]);
  });
});
