import { spawn } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile, mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { buildSandboxEnv, MAX_FILE_BYTES, type ExecResult, type SandboxFactory, type SandboxPort } from "@open-computer/agent-core";

export type SandboxKind = "bubblewrap" | "space" | "cube" | "none";

/** Resolves a path the model gave against the workspace and refuses anything that leaves it. */
export function inside(root: string, p: string): string {
  const resolved = path.resolve(root, p === "/workspace" || p.startsWith("/workspace/") ? p.slice("/workspace".length).replace(/^\/+/, "") || "." : p);
  if (resolved !== root && !resolved.startsWith(root + path.sep)) throw new Error("That path is outside the workspace.");
  return resolved;
}

export interface BubblewrapOptions {
  bwrap?: string;
  /** Parent directory for per-task workspaces. */
  root?: string;
  /** Extra read-only bind mounts, for example a toolchain directory. */
  readOnly?: string[];
}

/** Arguments for one sandboxed command: no network, no host home, a fresh /tmp, the workspace mounted read-write. */
export function bwrapArgs(workspace: string, command: string, readOnly: string[] = []): string[] {
  const env = buildSandboxEnv({ set: { HOME: "/workspace" } });
  const args = ["--unshare-all", "--die-with-parent", "--new-session", "--clearenv", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"];
  for (const d of ["/usr", "/bin", "/lib", "/lib64", "/sbin", "/etc/alternatives", ...readOnly]) args.push("--ro-bind-try", d, d);
  args.push("--ro-bind-try", "/etc/ssl", "/etc/ssl", "--bind", workspace, "/workspace", "--chdir", "/workspace");
  for (const [k, v] of Object.entries(env)) args.push("--setenv", k, v);
  args.push("sh", "-c", command);
  return args;
}

/** A local sandbox on Linux using bubblewrap. Each task gets its own directory, removed on destroy. */
export function bubblewrapFactory(o: BubblewrapOptions = {}): SandboxFactory {
  const bwrap = o.bwrap ?? "bwrap";
  return async (taskId) => {
    const base = o.root ?? tmpdir();
    await mkdir(base, { recursive: true });
    const workspace = await mkdtemp(path.join(base, `oc-${taskId.slice(0, 8)}-`));
    const port: SandboxPort = {
      exec: (command, options) =>
        new Promise<ExecResult>((resolve) => {
          const child = spawn(bwrap, bwrapArgs(workspace, command, o.readOnly), { stdio: ["ignore", "pipe", "pipe"] });
          let stdout = "";
          let stderr = "";
          let timedOut = false;
          const cap = 1024 * 1024;
          child.stdout.on("data", (d: Buffer) => void (stdout.length < cap && (stdout += d.toString())));
          child.stderr.on("data", (d: Buffer) => void (stderr.length < cap && (stderr += d.toString())));
          const timer = options?.timeoutMs
            ? setTimeout(() => {
                timedOut = true;
                child.kill("SIGKILL");
              }, options.timeoutMs)
            : undefined;
          child.on("error", () => resolve({ stdout: "", stderr: "bubblewrap could not be started", exitCode: 127 }));
          child.on("close", (code) => {
            if (timer) clearTimeout(timer);
            resolve({ stdout, stderr, exitCode: timedOut ? 124 : (code ?? 1), ...(timedOut ? { timedOut: true } : {}) });
          });
        }),
      readFile: async (p) => {
        const file = inside(workspace, p);
        const text = await readFile(file, "utf8");
        if (text.length > MAX_FILE_BYTES) throw new Error("The file is too large to read.");
        return text;
      },
      writeFile: async (p, content) => {
        const file = inside(workspace, p);
        await mkdir(path.dirname(file), { recursive: true });
        await writeFile(file, content);
      },
      destroy: async () => {
        await rm(workspace, { recursive: true, force: true });
      },
    };
    return port;
  };
}

/** The slice of the E2B-compatible SDK the adapter uses (SPACE and Cube both speak it). */
export interface E2bSandboxLike {
  commands: { run(cmd: string, opts?: { timeoutMs?: number; cwd?: string }): Promise<{ exitCode: number; stdout: string; stderr: string }> };
  files: { read(path: string): Promise<string>; write(path: string, data: string): Promise<unknown> };
  kill(): Promise<unknown>;
}
export interface E2bConfig {
  apiUrl: string;
  apiKey: string;
  template: string;
  domain?: string;
  sandboxUrl?: string;
}
export type E2bCreate = (config: E2bConfig) => Promise<E2bSandboxLike>;

/** The real SDK, loaded only when a SPACE or Cube backend is chosen. */
export const createWithSdk: E2bCreate = async (c) => {
  const { Sandbox } = await import("e2b");
  return (await Sandbox.create(c.template, {
    apiKey: c.apiKey,
    apiUrl: c.apiUrl,
    ...(c.domain ? { domain: c.domain } : {}),
    ...(c.sandboxUrl ? { sandboxUrl: c.sandboxUrl } : {}),
  })) as unknown as E2bSandboxLike;
};

/** SPACE or Cube sandbox through the E2B-compatible API. A non-zero exit is a result, not an error. */
export function e2bFactory(config: E2bConfig, create: E2bCreate = createWithSdk): SandboxFactory {
  return async () => {
    const sb = await create(config);
    return {
      exec: async (command, options) => {
        try {
          const r = await sb.commands.run(command, { ...(options?.timeoutMs ? { timeoutMs: options.timeoutMs } : {}), ...(options?.cwd ? { cwd: options.cwd } : {}) });
          return { stdout: r.stdout, stderr: r.stderr, exitCode: r.exitCode };
        } catch (e) {
          const x = e as { exitCode?: number; stdout?: string; stderr?: string; name?: string };
          if (typeof x.exitCode === "number") return { stdout: x.stdout ?? "", stderr: x.stderr ?? "", exitCode: x.exitCode };
          if (x.name === "TimeoutError") return { stdout: "", stderr: "", exitCode: 124, timedOut: true };
          throw e;
        }
      },
      readFile: (p) => sb.files.read(p),
      writeFile: async (p, c) => void (await sb.files.write(p, c)),
      destroy: async () => void (await sb.kill().catch(() => undefined)),
    };
  };
}

export interface SandboxChoice {
  kind: SandboxKind;
  env: Record<string, string | undefined>;
  /** Override for tests. */
  create?: E2bCreate;
}

/** Builds the sandbox factory for the backend chosen in setup. `none` yields a factory that refuses with a plain reason. */
export function sandboxFactoryFor(c: SandboxChoice): SandboxFactory {
  switch (c.kind) {
    case "bubblewrap":
      return bubblewrapFactory({ root: c.env.OC_SANDBOX_ROOT });
    case "space":
    case "cube": {
      const p = c.kind === "space" ? "SPACE" : "CUBE";
      const apiUrl = c.env[`${p}_API_URL`];
      const apiKey = c.env[`${p}_API_KEY`];
      const template = c.env[`${p}_TEMPLATE_ID`];
      if (!apiUrl || !apiKey || !template) {
        return async () => {
          throw new Error(`The ${c.kind} sandbox needs ${p}_API_URL, ${p}_API_KEY and ${p}_TEMPLATE_ID.`);
        };
      }
      return e2bFactory({ apiUrl, apiKey, template, ...(c.env[`${p}_DOMAIN`] ? { domain: c.env[`${p}_DOMAIN`] as string } : {}), ...(c.env[`${p}_DATA_PLANE_URL`] ? { sandboxUrl: c.env[`${p}_DATA_PLANE_URL`] as string } : {}) }, c.create);
    }
    default:
      return async () => {
        throw new Error("No sandbox is configured, so commands cannot run on this core.");
      };
  }
}
