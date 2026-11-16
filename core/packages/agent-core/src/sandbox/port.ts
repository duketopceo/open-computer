import { requireString as str, type Tool } from "../agent/tools.ts";

export interface ExecResult {
  stdout: string;
  stderr: string;
  exitCode: number;
  /** True when the port stopped the command because it ran past `timeoutMs`. */
  timedOut?: boolean;
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
export const DEFAULT_EXEC_TIMEOUT_MS = 120_000;
export const MAX_EXEC_TIMEOUT_MS = 600_000;

export interface SandboxToolOptions {
  maxFileBytes?: number;
  execTimeoutMs?: number;
  /** How long past `timeoutMs` the tool waits for the port to stop a command before it gives up on it. */
  execGraceMs?: number;
}

/** One fresh sandbox per task id, created on first use and destroyed when the task reaches a terminal state. */
export class SandboxManager {
  private readonly factory: SandboxFactory;
  private readonly live = new Map<string, Promise<SandboxPort>>();
  private readonly released = new Set<string>();
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
  /**
   * Destroys the task's sandbox. The live map lives in one Durable Object instance, which can be evicted while
   * a task waits for an approval, so with no live entry the sandbox is addressed by task id and destroyed anyway.
   */
  async release(taskId: string): Promise<void> {
    if (this.released.has(taskId)) return;
    this.released.add(taskId);
    const p = this.live.get(taskId);
    this.live.delete(taskId);
    await (await (p ?? this.factory(taskId))).destroy();
  }
}

/** Sandbox tools. Exec and write ask for approval under the default policy; read does not. */
export function sandboxTools(manager: SandboxManager, taskId: string, options: SandboxToolOptions = {}): Tool[] {
  const maxFileBytes = options.maxFileBytes ?? MAX_FILE_BYTES;
  const defaultTimeoutMs = options.execTimeoutMs ?? DEFAULT_EXEC_TIMEOUT_MS;
  const graceMs = options.execGraceMs ?? 5000;
  return [
    {
      spec: {
        name: "sandbox.exec",
        description: "Run a shell command in this task's sandbox and return its output. It is stopped if it runs too long.",
        parameters: {
          type: "object",
          properties: {
            command: { type: "string" },
            timeout_seconds: { type: "number", description: `Optional limit, at most ${MAX_EXEC_TIMEOUT_MS / 1000} seconds.` },
          },
          required: ["command"],
        },
        risk: "exec",
      },
      run: async (args) => {
        const sb = await manager.acquire(taskId);
        const asked = (args as { timeout_seconds?: unknown } | null)?.timeout_seconds;
        const timeoutMs =
          typeof asked === "number" && asked > 0 ? Math.min(asked * 1000, MAX_EXEC_TIMEOUT_MS) : Math.min(defaultTimeoutMs, MAX_EXEC_TIMEOUT_MS);
        const stopped = new Error(`The command did not finish within ${Math.round(timeoutMs / 1000)} seconds and was stopped.`);
        let timer: ReturnType<typeof setTimeout> | undefined;
        const backstop = new Promise<never>((_, reject) => {
          timer = setTimeout(() => reject(stopped), timeoutMs + graceMs);
        });
        const r = await Promise.race([sb.exec(str(args, "command"), { timeoutMs }), backstop]).finally(() => clearTimeout(timer));
        if (r.timedOut) throw stopped;
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
