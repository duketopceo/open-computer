import { describe, expect, it } from "vitest";
import type { Runner } from "../src/control.ts";
import { currentVersion, updateCore } from "../src/update.ts";

const LIST = JSON.stringify([
  { created_on: "2026-11-16T02:00:00Z", versions: [{ version_id: "v-old", percentage: 100 }] },
  { created_on: "2026-11-16T03:00:00Z", versions: [{ version_id: "v-split-a", percentage: 30 }, { version_id: "v-new", percentage: 70 }] },
]);

function runner(script: Record<string, { code: number; out?: string }>) {
  const calls: string[][] = [];
  const run: Runner = async (args) => {
    calls.push(args);
    const key = args[0] === "d1" ? "migrate" : (args[0] as string);
    return { code: script[key]?.code ?? 0, out: script[key]?.out ?? "" };
  };
  return { run, calls };
}

const opts = (run: Runner, healthy: () => Promise<boolean>) => ({ run, config: "cfg.jsonc", workerName: "w", healthy, tries: 3, waitMs: 0, sleep: async () => {} });

describe("currentVersion", () => {
  it("picks the biggest slice of the newest deployment", () => {
    expect(currentVersion(`noise\n${LIST}`)).toBe("v-new");
  });
  it("returns null for output it cannot read", () => {
    expect(currentVersion("not json")).toBeNull();
  });
});

describe("updateCore", () => {
  it("reports success when the new version is healthy and does not roll back", async () => {
    const { run, calls } = runner({ deployments: { code: 0, out: LIST } });
    const r = await updateCore(opts(run, async () => true));
    expect(r.ok).toBe(true);
    expect(calls.map((c) => c[0])).toEqual(["deployments", "d1", "deploy"]);
  });

  it("rolls back to the previous version when the health check keeps failing, then rechecks", async () => {
    const { run, calls } = runner({ deployments: { code: 0, out: LIST } });
    let n = 0;
    const r = await updateCore(opts(run, async () => ++n > 3));
    expect(r.ok).toBe(false);
    expect(r.rolledBackTo).toBe("v-new");
    expect(r.message).toContain("restored and is healthy");
    expect(calls.at(-1)).toEqual(["rollback", "v-new", "-c", "cfg.jsonc", "--message", "oc update: health check failed"]);
  });

  it("does not deploy when the migration fails", async () => {
    const { run, calls } = runner({ deployments: { code: 0, out: LIST }, migrate: { code: 1 } });
    const r = await updateCore(opts(run, async () => true));
    expect(r.ok).toBe(false);
    expect(calls.some((c) => c[0] === "deploy")).toBe(false);
    expect(r.message).toContain("still running");
  });

  it("keeps the old version when the deploy itself fails", async () => {
    const { run, calls } = runner({ deployments: { code: 0, out: LIST }, deploy: { code: 1 } });
    const r = await updateCore(opts(run, async () => true));
    expect(r.ok).toBe(false);
    expect(calls.some((c) => c[0] === "rollback")).toBe(false);
  });

  it("says so plainly when it is unhealthy and there is nothing to go back to", async () => {
    const { run } = runner({ deployments: { code: 1 } });
    const r = await updateCore(opts(run, async () => false));
    expect(r.ok).toBe(false);
    expect(r.message).toContain("no previous version");
  });

  it("reports a failed rollback with the dashboard fallback", async () => {
    const { run } = runner({ deployments: { code: 0, out: LIST }, rollback: { code: 1 } });
    const r = await updateCore(opts(run, async () => false));
    expect(r.message).toContain("Cloudflare dashboard");
  });
});
