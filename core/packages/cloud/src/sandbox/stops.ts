// Why a sandbox stopped, recorded the way Cloudflare's "Run code when a sandbox stops" pattern does it
// (developers.cloudflare.com/sandbox/manage/run-code-when-a-sandbox-stops/): `container.monitor()` settles when the
// instance stops, and says how.
//
//   resolves                      the main process exited with code 0, or destroy() was called with no reason
//   rejects, error.exitCode       the main process exited with another code
//   rejects with a string         destroy(reason) was called: our own deliberate stop ("stopped" or "idle")
//   rejects with anything else    the container failed (or stopped for a reason Cloudflare describes)
//
// A stop is "clean" when the main process exited 0, "deliberate" when our code ended it with a reason, and "crashed" for
// anything else. Only crashes fail the diagnostics row.

export type StopKind = "clean" | "deliberate" | "crashed";

export interface StopRecord {
  /** `exited`, `stopped`, `idle`, `unknown`, or the error text Cloudflare gave. */
  reason: string;
  exit_code: number | null;
  kind: StopKind;
  stopped_at: string;
}

export function classifyStop(reason: string, exitCode: number | null): StopKind {
  if (reason === "exited" && exitCode === 0) return "clean";
  if (reason === "stopped" || reason === "idle") return "deliberate";
  return "crashed";
}

/** The part of the Durable Object's container the watcher needs (the real `Container` satisfies it). */
export interface WatchedContainer {
  readonly running: boolean;
  monitor(): Promise<void>;
  destroy(error?: unknown): Promise<void>;
  signal(signo: number): void;
}

export interface WatcherOptions {
  /** How long a graceful stop (SIGTERM, then the main process exits 0) may take before the deliberate fallback. */
  graceMs?: number;
  now?: () => number;
  wait?: (ms: number) => Promise<void>;
}

const SIGTERM = 15;

/** Watches one container instance, records each stop once, and ends it so the stop is clean. */
export class StopWatcher {
  lastStop: StopRecord | null = null;
  private watching = false;
  private recorded: Promise<void> = Promise.resolve();
  private readonly graceMs: number;
  private readonly now: () => number;
  private readonly wait: (ms: number) => Promise<void>;

  constructor(
    private readonly container: WatchedContainer,
    private readonly onStop: (record: StopRecord) => Promise<void> | void,
    options: WatcherOptions = {},
  ) {
    this.graceMs = options.graceMs ?? 8_000;
    this.now = options.now ?? Date.now;
    this.wait = options.wait ?? ((ms) => new Promise((r) => setTimeout(r, ms)));
  }

  /** Starts watching the running instance. A second call while one watch is pending does nothing. */
  watch(): void {
    if (this.watching) return;
    this.watching = true;
    this.lastStop = null;
    this.container.monitor().then(
      () => this.stopped("exited", 0),
      (error: unknown) => {
        if (typeof error === "string") return this.stopped(error, null);
        const exitCode = (error as { exitCode?: unknown } | null)?.exitCode;
        if (typeof exitCode === "number") return this.stopped("exited", exitCode);
        return this.stopped(String(error instanceof Error ? error.message : error).slice(0, 200), null);
      },
    );
  }

  /** Records a stop that `monitor()` missed (the Durable Object was out of memory when the instance stopped). */
  reconcile(): void {
    if (this.watching && !this.container.running && this.lastStop === null) this.stopped("unknown", null);
  }

  private stopped(reason: string, exitCode: number | null): void {
    if (!this.watching) return;
    this.watching = false;
    const record: StopRecord = { reason, exit_code: exitCode, kind: classifyStop(reason, exitCode), stopped_at: new Date(this.now()).toISOString() };
    this.lastStop = record;
    this.recorded = Promise.resolve(this.onStop(record)).catch(() => undefined);
  }

  /** Resolves once the last stop has been recorded. */
  settled(): Promise<void> {
    return this.recorded;
  }

  /**
   * Ends the instance so that it counts as a clean stop: SIGTERM to the main process, which exits 0 (the image's entrypoint traps
   * it). Only when it does not stop in `graceMs` is it destroyed with the reason "stopped", which is still a recorded, deliberate
   * stop and never a crash.
   */
  async stopCleanly(): Promise<StopKind | null> {
    if (!this.container.running) {
      await this.recorded;
      return this.lastStop?.kind ?? null;
    }
    this.container.signal(SIGTERM);
    const deadline = this.now() + this.graceMs;
    while (this.container.running && this.now() < deadline) await this.wait(50);
    if (this.container.running) await this.container.destroy("stopped");
    // monitor() settles a moment after the instance is gone.
    for (let i = 0; i < 40 && this.lastStop === null; i++) await this.wait(25);
    await this.recorded;
    return this.lastStop?.kind ?? null;
  }
}

/** The stop records in an audit log (`sandbox.stopped` entries), newest last, at most `limit`. */
export function stopsFromAudit(records: Array<{ kind: string; task_id?: string | null; ts?: string; data?: unknown }>, limit = 20): Array<StopRecord & { task_id: string | null }> {
  return records
    .filter((r) => r.kind === "sandbox.stopped")
    .slice(-limit)
    .map((r) => {
      const d = (r.data ?? {}) as Partial<StopRecord>;
      const reason = typeof d.reason === "string" ? d.reason : "unknown";
      const exit_code = typeof d.exit_code === "number" ? d.exit_code : null;
      return { task_id: r.task_id ?? null, reason, exit_code, kind: classifyStop(reason, exit_code), stopped_at: typeof d.stopped_at === "string" ? d.stopped_at : (r.ts ?? "") };
    });
}
