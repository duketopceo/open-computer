import { describe, expect, it } from "vitest";
import { parseDaemonOptions } from "../src/daemon.ts";

describe("daemon options", () => {
  it("is off without the flag, so the stdin-supervised sidecar is unchanged", () => {
    expect(parseDaemonOptions([], { OC_CORE_TOKEN_FILE: "/x" })).toBeNull();
  });
  it("needs a token file and defaults the port", () => {
    expect(() => parseDaemonOptions(["--daemon"], {})).toThrow(/OC_CORE_TOKEN_FILE/);
    expect(parseDaemonOptions(["--daemon"], { OC_CORE_TOKEN_FILE: "/etc/t" })).toEqual({ tokenFile: "/etc/t", port: 8787 });
  });
  it("rejects a bad port", () => {
    for (const p of ["0", "70000", "abc", "1.5"]) {
      expect(() => parseDaemonOptions(["--daemon"], { OC_CORE_TOKEN_FILE: "/x", OC_CORE_PORT: p })).toThrow(/port/);
    }
  });
});
