import { Files } from "@cloudflare/sandbox";
import { DurableObject, WorkerEntrypoint } from "cloudflare:workers";
import { buildSandboxEnv } from "@open-computer/agent-core";
import type { Env } from "../env.ts";
import { ledgerStub } from "../storage/ledger-client.ts";
import { createOutboundHandler, type OutboundRule } from "./outbound.ts";
import { MAIN_PROCESS } from "./main-process.ts";
import { StopWatcher, type StopRecord } from "./stops.ts";
import type { ExecResult } from "./port.ts";

const decoder = new TextDecoder();
const START_WAIT_MS = 60_000;

/**
 * The Container behind one task's sandbox (class name `Sandbox`, bound as SANDBOX). It starts the instance
 * with internet off and an allowlisted environment; all outbound traffic goes through OutboundProxy.
 * Containers do not run under miniflare, so this class is exercised only by an owner-approved real run.
 */
export class Sandbox extends DurableObject<Env> {
  private readonly stops: StopWatcher;
  private taskId = "";

  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    this.stops = new StopWatcher(ctx.container as unknown as ConstructorParameters<typeof StopWatcher>[0], (record) => this.recordStop(record));
    // A restarted Durable Object loses the earlier monitor() call; pick the running instance up again.
    if (ctx.container?.running) void ctx.blockConcurrencyWhile(async () => {
      this.taskId = (await ctx.storage.get<string>("taskId")) ?? "";
      this.stops.watch();
    });
  }

  /** Keeps the stop on the Durable Object and in the audit log, so Diagnostics can list the last ones. */
  private async recordStop(record: StopRecord): Promise<void> {
    await this.ctx.storage.put("lastStop", record);
    try {
      await ledgerStub(this.env).appendAudit("sandbox.stopped", this.taskId || null, { ...record });
    } catch {
      /* the record on this object still holds the stop */
    }
  }

  async lastStop(): Promise<StopRecord | null> {
    this.stops.reconcile();
    await this.stops.settled();
    return this.stops.lastStop ?? (await this.ctx.storage.get<StopRecord>("lastStop")) ?? null;
  }

  private async ensureStarted(taskId: string): Promise<Container> {
    const container = this.ctx.container;
    if (!container) throw new Error("The container binding is not configured.");
    this.taskId = taskId;
    if (!container.running) {
      await this.ctx.storage.put("taskId", taskId);
      container.start({
        image: container.images.sandbox ?? Object.values(container.images)[0] ?? "",
        instance: "lite",
        // The image's default command is not a long-running process; commands arrive through exec().
        entrypoint: MAIN_PROCESS,
        enableInternet: false,
        env: buildSandboxEnv({ set: { OC_TASK_ID: taskId } }),
      });
      await container.interceptOutboundHttps("*:443", this.ctx.exports.OutboundProxy({ props: { taskId } }));
      this.stops.watch();
    }
    // start() returns before the instance is up; commands only work once it reports running.
    const deadline = Date.now() + START_WAIT_MS;
    while (!container.running) {
      const stop = this.stops.lastStop;
      if (stop) throw new Error(`The container stopped right after it started (${stop.reason}${stop.exit_code === null ? "" : `, exit code ${stop.exit_code}`}).`.slice(0, 240));
      if (Date.now() > deadline) throw new Error("The container did not start in time.");
      await new Promise((r) => setTimeout(r, 100));
    }
    return container;
  }

  async run(taskId: string, command: string, cwd = "/workspace", timeoutMs?: number): Promise<ExecResult> {
    const container = await this.ensureStarted(taskId);
    const proc = await container.exec(["sh", "-c", command], { cwd });
    let timer: ReturnType<typeof setTimeout> | undefined;
    const timedOut = new Promise<"timeout">((resolve) => {
      if (timeoutMs !== undefined) timer = setTimeout(() => resolve("timeout"), timeoutMs);
    });
    const finished = await Promise.race([proc.output(), timedOut]).finally(() => clearTimeout(timer));
    if (finished === "timeout") {
      proc.kill();
      return { stdout: "", stderr: "", exitCode: 124, timedOut: true };
    }
    return { stdout: decoder.decode(finished.stdout), stderr: decoder.decode(finished.stderr), exitCode: finished.exitCode };
  }

  async readText(taskId: string, path: string): Promise<string> {
    const container = await this.ensureStarted(taskId);
    return (await new Files(container).readFile(path)).text();
  }

  async writeText(taskId: string, path: string, content: string): Promise<void> {
    const container = await this.ensureStarted(taskId);
    await new Files(container).writeFile(path, content);
  }

  /** Ends the instance as a clean stop: SIGTERM and the main process exits 0; destroyed with a reason only if it does not. */
  async destroyContainer(): Promise<void> {
    if (!this.ctx.container) return;
    await this.stops.stopCleanly();
  }
}

/** Outbound credential handler bound to the Container. Hosts and secret names are configuration, not code. */
export class OutboundProxy extends WorkerEntrypoint<Env, { taskId: string }> {
  override async fetch(request: Request): Promise<Response> {
    const rules: OutboundRule[] = []; // No credentialed hosts are configured: every request is denied and audited.
    const handler = createOutboundHandler({
      rules,
      upstream: (r) => fetch(r),
      audit: async (kind, data) => {
        await ledgerStub(this.env).appendAudit(kind, this.ctx.props.taskId, data);
      },
    });
    return handler(request);
  }
}
