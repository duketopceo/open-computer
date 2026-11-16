import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { chooseSandbox, defaultStateDir, runtimeFromEnv } from "../src/runtime/boot.ts";

describe("sandbox choice", () => {
  it("uses bubblewrap when it is installed and none when it is not", () => {
    expect(chooseSandbox({}, () => true)).toBe("bubblewrap");
    expect(chooseSandbox({}, () => false)).toBe("none");
  });
  it("honours OC_SANDBOX and rejects unknown values", () => {
    expect(chooseSandbox({ OC_SANDBOX: "space" }, () => true)).toBe("space");
    expect(chooseSandbox({ OC_SANDBOX: "cube" }, () => false)).toBe("cube");
    expect(() => chooseSandbox({ OC_SANDBOX: "docker" })).toThrow(/OC_SANDBOX must be one of/);
  });
});

describe("runtime from the environment", () => {
  it("keeps state under OC_CORE_STATE_DIR and carries the policy through", () => {
    const dir = mkdtempSync(path.join(tmpdir(), "oc-boot-"));
    expect(defaultStateDir({ OC_CORE_STATE_DIR: dir })).toBe(dir);
    const rt = runtimeFromEnv({ OC_CORE_STATE_DIR: dir, OC_SANDBOX: "none", OC_POLICY_JSON: '{"max_daily_cap_usd": 3}', OC_CORE_MODEL_KEY_FILE: "/run/k" });
    expect(rt.sandboxKind).toBe("none");
    expect(rt.policyJson).toBe('{"max_daily_cap_usd": 3}');
    expect(rt.modelKeyFile).toBe("/run/k");
    rt.store.setSetting("a", "b");
    expect(rt.store.getSetting("a")).toBe("b");
    rt.store.close();
  });
});
