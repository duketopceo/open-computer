import { env } from "./env.ts";
import { beforeEach, describe, expect, it } from "vitest";
import { classifyStop, StopWatcher, stopsFromAudit, type StopRecord, type WatchedContainer } from "../src/sandbox/stops.ts";
import { ledgerStub } from "../src/storage/ledger-client.ts";
import type { Doubles } from "./entry.ts";
import { api, install, seedSecrets } from "./support.ts";

beforeEach(async () => {
  await seedSecrets();
});

/** A container whose end the test decides: how it ends, and what monitor() then says. */
function fakeContainer(onSignal: "exit0" | "ignore" | "crash137" = "exit0") {
  let running = true;
  let settle!: { resolve: () => void; reject: (e: unknown) => void };
  const monitored = new Promise<void>((resolve, reject) => (settle = { resolve, reject }));
  const calls: string[] = [];
  const c: WatchedContainer & { calls: string[]; exitWith(code: number): void; vanish(): void } = {
    get running() {
      return running;
    },
    monitor: () => monitored,
    signal: (n) => {
      calls.push(`signal ${n}`);
      if (onSignal === "exit0") {
        running = false;
        settle.resolve();
      } else if (onSignal === "crash137") {
        running = false;
        settle.reject(Object.assign(new Error("killed"), { exitCode: 137 }));
      }
    },
    destroy: async (error?: unknown) => {
      calls.push(`destroy ${String(error)}`);
      running = false;
      if (error === undefined) settle.resolve();
      else settle.reject(error);
    },
    calls,
    exitWith(code: number) {
      running = false;
      if (code === 0) settle.resolve();
      else settle.reject(Object.assign(new Error(`exit ${code}`), { exitCode: code }));
    },
    vanish() {
      running = false; // stopped while nobody was watching: monitor() never settles
    },
  };
  return c;
}

const watcher = (c: WatchedContainer, seen: StopRecord[] = []) =>
  new StopWatcher(c, (r) => void seen.push(r), { graceMs: 60, wait: (ms) => new Promise((r) => setTimeout(r, Math.min(ms, 5))) });

describe("how a sandbox stop is classified", () => {
  it("an exit with code 0 is clean, our own reasons are deliberate, everything else is a crash", () => {
    expect(classifyStop("exited", 0)).toBe("clean");
    expect(classifyStop("stopped", null)).toBe("deliberate");
    expect(classifyStop("idle", null)).toBe("deliberate");
    expect(classifyStop("exited", 137)).toBe("crashed");
    expect(classifyStop("exited", 1)).toBe("crashed");
    expect(classifyStop("unknown", null)).toBe("crashed");
    expect(classifyStop("The container failed to start", null)).toBe("crashed");
  });
});

describe("the stop watcher", () => {
  it("ends a sandbox with SIGTERM so the main process exits 0, and records a clean stop", async () => {
    const seen: StopRecord[] = [];
    const c = fakeContainer("exit0");
    const w = watcher(c, seen);
    w.watch();
    expect(await w.stopCleanly()).toBe("clean");
    expect(c.calls).toEqual(["signal 15"]);
    expect(seen).toHaveLength(1);
    expect(seen[0]).toMatchObject({ reason: "exited", exit_code: 0, kind: "clean" });
    expect(w.lastStop?.kind).toBe("clean");
  });

  it("falls back to destroy with a reason, a deliberate stop, when the main process ignores SIGTERM", async () => {
    const seen: StopRecord[] = [];
    const c = fakeContainer("ignore");
    const w = watcher(c, seen);
    w.watch();
    expect(await w.stopCleanly()).toBe("deliberate");
    expect(c.calls).toEqual(["signal 15", "destroy stopped"]);
    expect(seen[0]).toMatchObject({ reason: "stopped", exit_code: null, kind: "deliberate" });
  });

  it("records a crash with its exit code, and a failure with Cloudflare's own text", async () => {
    const seen: StopRecord[] = [];
    const c = fakeContainer();
    const w = watcher(c, seen);
    w.watch();
    c.exitWith(139);
    await w.settled();
    expect(seen[0]).toMatchObject({ reason: "exited", exit_code: 139, kind: "crashed" });

    const failing: WatchedContainer = { running: false, monitor: () => Promise.reject(new Error("out of memory")), destroy: async () => {}, signal: () => {} };
    const seen2: StopRecord[] = [];
    const w2 = watcher(failing, seen2);
    w2.watch();
    await new Promise((r) => setTimeout(r, 10));
    expect(seen2[0]).toMatchObject({ reason: "out of memory", exit_code: null, kind: "crashed" });
  });

  it("records a stop once, and one that happened while nobody watched as unknown", async () => {
    const seen: StopRecord[] = [];
    const c = fakeContainer();
    const w = watcher(c, seen);
    w.watch();
    w.watch(); // a second call while one is pending does nothing
    c.exitWith(0);
    await w.settled();
    c.exitWith(1); // too late: already recorded
    await w.settled();
    expect(seen).toHaveLength(1);

    const gone = fakeContainer();
    const seen3: StopRecord[] = [];
    const w3 = watcher(gone, seen3);
    w3.watch();
    gone.vanish();
    w3.reconcile();
    await w3.settled();
    expect(seen3[0]).toMatchObject({ reason: "unknown", kind: "crashed" });
  });

  it("a stop of a sandbox that is already gone just reports what was recorded", async () => {
    const seen: StopRecord[] = [];
    const c = fakeContainer();
    const w = watcher(c, seen);
    w.watch();
    c.exitWith(0);
    await w.settled();
    expect(await w.stopCleanly()).toBe("clean");
    expect(c.calls).toEqual([]);
  });
});

describe("stops in the audit log", () => {
  it("keeps the last twenty, newest last, and reads kind from reason and exit code", () => {
    const rec = (i: number, data: object) => ({ kind: "sandbox.stopped", task_id: `t${i}`, ts: "2026-11-16T00:00:00Z", data });
    const log = [
      { kind: "event:usage", task_id: "x", data: {} },
      ...Array.from({ length: 25 }, (_, i) => rec(i, { reason: "exited", exit_code: 0, stopped_at: "2026-11-16T01:00:00Z" })),
      rec(99, { reason: "exited", exit_code: 137 }),
      rec(100, { reason: "stopped" }),
    ];
    const stops = stopsFromAudit(log);
    expect(stops).toHaveLength(20);
    expect(stops.at(-1)).toMatchObject({ task_id: "t100", reason: "stopped", kind: "deliberate" });
    expect(stops.at(-2)).toMatchObject({ task_id: "t99", exit_code: 137, kind: "crashed" });
  });
});

const doubles = (): Doubles => (globalThis as unknown as { __doubles: Doubles }).__doubles;

describe("the diagnostics routes", () => {
  it("the sandbox check passes with a clean stop and says how it stopped, and fails on a crash or a missing record", async () => {
    install([]);
    const make = (stop: StopRecord | null) => () => ({
      exec: async (cmd: string) => ({ stdout: cmd === "env" ? "PATH=/bin\n" : cmd.includes("curl") ? "000\n000" : "oc-sandbox-ok\n", stderr: "", exitCode: 0 }),
      readFile: async () => "",
      writeFile: async () => {},
      destroy: async () => {},
      stopRecord: async () => stop,
    });
    const at = "2026-11-16T00:00:00Z";
    doubles().sandbox = make({ reason: "exited", exit_code: 0, kind: "clean", stopped_at: at });
    const clean = (await (await api("/v1/diag/sandbox", { method: "POST" })).json()) as Record<string, unknown>;
    expect(clean).toMatchObject({ ok: true, stop: "clean" });

    doubles().sandbox = make({ reason: "exited", exit_code: 137, kind: "crashed", stopped_at: at });
    const crashed = (await (await api("/v1/diag/sandbox", { method: "POST" })).json()) as Record<string, unknown>;
    expect(crashed).toMatchObject({ ok: false, code: "sandbox_unclean_stop", stop: "crashed" });
    expect(String(crashed.message)).toContain("exit code 137");

    doubles().sandbox = make(null);
    const missing = (await (await api("/v1/diag/sandbox", { method: "POST" })).json()) as Record<string, unknown>;
    expect(missing).toMatchObject({ ok: false, code: "sandbox_unclean_stop", stop: "unknown" });

    // A deliberate stop (destroyed with a reason) is not a crash.
    doubles().sandbox = make({ reason: "stopped", exit_code: null, kind: "deliberate", stopped_at: at });
    expect(((await (await api("/v1/diag/sandbox", { method: "POST" })).json()) as Record<string, unknown>)).toMatchObject({ ok: true, stop: "deliberate" });
  });

  it("lists the last stops from the audit log, ok while none crashed and failing the row when one did", async () => {
    install([]);
    const ledger = ledgerStub(env);
    const empty = (await (await api("/v1/diag/sandbox/stops")).json()) as Record<string, any>;
    expect(empty).toMatchObject({ ok: true, total: 0, crashed: 0 });
    await ledger.appendAudit("sandbox.stopped", "diag-1", { reason: "exited", exit_code: 0, kind: "clean", stopped_at: "2026-11-16T00:00:00Z" });
    await ledger.appendAudit("sandbox.stopped", "t-2", { reason: "stopped", exit_code: null, kind: "deliberate", stopped_at: "2026-11-16T00:01:00Z" });
    const healthy = (await (await api("/v1/diag/sandbox/stops")).json()) as Record<string, any>;
    expect(healthy).toMatchObject({ ok: true, total: 2, clean: 1, deliberate: 1, crashed: 0 });
    expect(healthy.stops[0]).toMatchObject({ task_id: "diag-1", reason: "exited", exit_code: 0, kind: "clean" });
    await ledger.appendAudit("sandbox.stopped", "t-3", { reason: "exited", exit_code: 137, kind: "crashed", stopped_at: "2026-11-16T00:02:00Z" });
    const bad = (await (await api("/v1/diag/sandbox/stops")).json()) as Record<string, any>;
    expect(bad).toMatchObject({ ok: false, crashed: 1 });
    expect(bad.message).toContain("exit 137");
  });

  it("needs the token like every other data route", async () => {
    install([]);
    expect((await api("/v1/diag/sandbox/stops", { token: null })).status).toBe(401);
  });
});
