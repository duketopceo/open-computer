import { Files } from "@cloudflare/sandbox";
import { DurableObject, WorkerEntrypoint } from "cloudflare:workers";
import { buildSandboxEnv } from "@open-computer/agent-core";
import type { Env } from "../env.ts";
import { createOutboundHandler, type OutboundRule } from "./outbound.ts";
import type { ExecResult } from "./port.ts";

const decoder = new TextDecoder();

/**
 * The Container behind one task's sandbox (class name `Sandbox`, bound as SANDBOX). It starts the instance
 * with internet off and an allowlisted environment; all outbound traffic goes through OutboundProxy.
 * Containers do not run under miniflare, so this class is exercised only by an owner-approved real run.
 */
export class Sandbox extends DurableObject<Env> {
  private async ensureStarted(taskId: string): Promise<Container> {
    const container = this.ctx.container;
    if (!container) throw new Error("The container binding is not configured.");
    if (!container.running) {
      container.start({
        image: container.images.sandbox ?? Object.values(container.images)[0] ?? "",
        enableInternet: false,
        env: buildSandboxEnv({ set: { OC_TASK_ID: taskId } }),
      });
      await container.interceptOutboundHttps("*:443", this.ctx.exports.OutboundProxy({ props: { taskId } }));
    }
    return container;
  }

  async run(taskId: string, command: string, cwd = "/workspace"): Promise<ExecResult> {
    const container = await this.ensureStarted(taskId);
    const proc = await container.exec(["sh", "-c", command], { cwd });
    const out = await proc.output();
    return { stdout: decoder.decode(out.stdout), stderr: decoder.decode(out.stderr), exitCode: out.exitCode };
  }

  async readText(taskId: string, path: string): Promise<string> {
    const container = await this.ensureStarted(taskId);
    return (await new Files(container).readFile(path)).text();
  }

  async writeText(taskId: string, path: string, content: string): Promise<void> {
    const container = await this.ensureStarted(taskId);
    await new Files(container).writeFile(path, content);
  }

  async destroyContainer(): Promise<void> {
    await this.ctx.container?.destroy();
  }
}

/** Outbound credential handler bound to the Container. Hosts and secret names are configuration, not code. */
export class OutboundProxy extends WorkerEntrypoint<Env, { taskId: string }> {
  override async fetch(request: Request): Promise<Response> {
    const rules: OutboundRule[] = []; // First PR: no credentialed hosts are enabled; every request is denied and audited.
    const handler = createOutboundHandler({
      rules,
      upstream: (r) => fetch(r),
      audit: async (kind, data) => {
        const ledger = this.env.LEDGER.get(this.env.LEDGER.idFromName("ledger"));
        await ledger.appendAudit(kind, this.ctx.props.taskId, data);
      },
    });
    return handler(request);
  }
}
