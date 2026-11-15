import type { Tool } from "@open-computer/agent-core";

export interface ExecResult {
  stdout: string;
  stderr: string;
  exitCode: number;
}

/** What the agent needs from a sandbox. The Cloudflare Container implements it; tests use a fake. */
export interface SandboxPort {
  exec(command: string, options?: { cwd?: string; timeoutMs?: number }): Promise<ExecResult>;
  readFile(path: string): Promise<string>;
  writeFile(path: string, content: string): Promise<void>;
  destroy(): Promise<void>;
}

export type SandboxFactory = (taskId: string) => SandboxPort | Promise<SandboxPort>;

export const MAX_FILE_BYTES = 1024 * 1024;

/** One fresh sandbox per task id, created on first use and destroyed when the task reaches a terminal state. */
export class SandboxManager {
  private readonly factory: SandboxFactory;
  private readonly live = new Map<string, Promise<SandboxPort>>();
  constructor(factory: SandboxFactory) {
    this.factory = factory;
  }
  acquire(taskId: string): Promise<SandboxPort> {
    let p = this.live.get(taskId);
    if (!p) {
      p = Promise.resolve(this.factory(taskId));
      this.live.set(taskId, p);
    }
    return p;
  }
  async release(taskId: string): Promise<void> {
    const p = this.live.get(taskId);
    if (!p) return;
    this.live.delete(taskId);
    await (await p).destroy();
  }
}

function str(args: unknown, key: string): string {
  const v = (args as Record<string, unknown> | null)?.[key];
  if (typeof v !== "string") throw new Error(`"${key}" must be a string.`);
  return v;
}

/** Sandbox tools. Exec and write ask for approval under the default policy; read does not. */
export function sandboxTools(manager: SandboxManager, taskId: string, maxFileBytes = MAX_FILE_BYTES): Tool[] {
  return [
    {
      spec: {
        name: "sandbox.exec",
        description: "Run a shell command in this task's sandbox and return its output.",
        parameters: { type: "object", properties: { command: { type: "string" } }, required: ["command"] },
        risk: "exec",
      },
      run: async (args) => {
        const sb = await manager.acquire(taskId);
        const r = await sb.exec(str(args, "command"));
        const text = `exit code ${r.exitCode}\n--- stdout\n${r.stdout}\n--- stderr\n${r.stderr}`;
        if (r.exitCode !== 0) throw new Error(text);
        return text;
      },
    },
    {
      spec: {
        name: "sandbox.read_file",
        description: "Read a text file from this task's sandbox.",
        parameters: { type: "object", properties: { path: { type: "string" } }, required: ["path"] },
        risk: "read",
      },
      run: async (args) => (await manager.acquire(taskId)).readFile(str(args, "path")),
    },
    {
      spec: {
        name: "sandbox.write_file",
        description: "Write a text file in this task's sandbox.",
        parameters: {
          type: "object",
          properties: { path: { type: "string" }, content: { type: "string" } },
          required: ["path", "content"],
        },
        risk: "write",
      },
      run: async (args) => {
        const content = str(args, "content");
        const bytes = new TextEncoder().encode(content).byteLength;
        if (bytes > maxFileBytes) throw new Error(`The file is ${bytes} bytes; the limit is ${maxFileBytes}.`);
        await (await manager.acquire(taskId)).writeFile(str(args, "path"), content);
        return `Wrote ${bytes} bytes.`;
      },
    },
  ];
}
