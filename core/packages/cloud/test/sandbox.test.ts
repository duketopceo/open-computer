import { describe, expect, it } from "vitest";
import { buildSandboxEnv } from "@open-computer/agent-core";
import { createOutboundHandler, OutboundConfigError, PROVIDER_HOSTS } from "../src/sandbox/outbound.ts";
import { SandboxManager, sandboxTools, type SandboxPort } from "../src/sandbox/port.ts";
import { FakeSandbox, MODEL_KEY, TOKEN } from "./support.ts";

const ctx = { taskId: "t", callId: "c", emit: () => {} };
const tool = (manager: SandboxManager, name: string) => {
  const t = sandboxTools(manager, "t", { maxFileBytes: 100, execGraceMs: 20 }).find((x) => x.spec.name === name);
  if (!t) throw new Error(name);
  return t;
};

describe("sandbox manager and tools", () => {
  it("gives each task id its own new sandbox, reuses it within the task, and destroys it on release", async () => {
    const made: FakeSandbox[] = [];
    const m = new SandboxManager(() => {
      const s = new FakeSandbox();
      made.push(s);
      return s;
    });
    const a1 = await m.acquire("task-a");
    const a2 = await m.acquire("task-a");
    const b = await m.acquire("task-b");
    expect(a1).toBe(a2);
    expect(a1).not.toBe(b);
    expect(made).toHaveLength(2);
    await m.release("task-a");
    expect(made[0]?.destroyed).toBe(true);
    expect(made[1]?.destroyed).toBe(false);
    await m.release("task-a"); // releasing twice is harmless
  });

  it("destroys a task's sandbox by task id when this instance never held it (the Durable Object was evicted)", async () => {
    const made: FakeSandbox[] = [];
    const m = new SandboxManager(() => {
      const s = new FakeSandbox();
      made.push(s);
      return s;
    });
    await m.release("task-from-another-instance");
    expect(made).toHaveLength(1);
    expect(made[0]?.destroyed).toBe(true);
    await m.release("task-from-another-instance"); // and only once
    expect(made).toHaveLength(1);
  });

  it("returns stdout, stderr and the exit code, and turns a nonzero exit into an error observation", async () => {
    const m = new SandboxManager(() => ({
      exec: async (cmd: string) => (cmd === "false" ? { stdout: "", stderr: "boom", exitCode: 1 } : { stdout: "ok", stderr: "", exitCode: 0 }),
      readFile: async () => "",
      writeFile: async () => {},
      destroy: async () => {},
    }));
    const exec = tool(m, "sandbox.exec");
    expect(await exec.run({ command: "true" }, ctx)).toContain("exit code 0\n--- stdout\nok");
    await expect(exec.run({ command: "false" }, ctx)).rejects.toThrow(/exit code 1[\s\S]*boom/);
  });

  it("Covers AE3: a command that prints the sandbox environment shows no secret value", async () => {
    const hostEnv = { PATH: "/usr/bin", HOME: "/root", MODEL_KEY, ACCESS_SECRET: TOKEN };
    const sandboxEnv = buildSandboxEnv({ source: hostEnv, secrets: [MODEL_KEY, TOKEN], set: { OC_TASK_ID: "t" } });
    const printing: SandboxPort = {
      exec: async () => ({ stdout: Object.entries(sandboxEnv).map(([k, v]) => `${k}=${v}`).join("\n"), stderr: "", exitCode: 0 }),
      readFile: async () => "",
      writeFile: async () => {},
      destroy: async () => {},
    };
    const out = await tool(new SandboxManager(() => printing), "sandbox.exec").run({ command: "env" }, ctx);
    expect(out).not.toContain(MODEL_KEY);
    expect(out).not.toContain(TOKEN);
    expect(out).toContain("OC_TASK_ID=t");
  });

  it("rejects a file write over the size limit and accepts one under it", async () => {
    const writes: string[] = [];
    const m = new SandboxManager(() => ({
      exec: async () => ({ stdout: "", stderr: "", exitCode: 0 }),
      readFile: async () => "",
      writeFile: async (p: string) => void writes.push(p),
      destroy: async () => {},
    }));
    const write = tool(m, "sandbox.write_file");
    await expect(write.run({ path: "/workspace/big", content: "x".repeat(101) }, ctx)).rejects.toThrow(/limit is 100/);
    expect(await write.run({ path: "/workspace/small", content: "x".repeat(100) }, ctx)).toContain("Wrote 100 bytes");
    expect(writes).toEqual(["/workspace/small"]);
  });

  describe("exec timeout", () => {
    const recording = (result: () => Promise<{ stdout: string; stderr: string; exitCode: number; timedOut?: boolean }>) => {
      const seen: Array<number | undefined> = [];
      const manager = new SandboxManager(() => ({
        exec: async (_cmd: string, options?: { timeoutMs?: number }) => (seen.push(options?.timeoutMs), result()),
        readFile: async () => "",
        writeFile: async () => {},
        destroy: async () => {},
      }));
      return { seen, manager };
    };
    const ok = async () => ({ stdout: "ok", stderr: "", exitCode: 0 });
    const exec = (manager: SandboxManager, options = {}) => {
      const t = sandboxTools(manager, "t", options).find((x) => x.spec.name === "sandbox.exec");
      if (!t) throw new Error("no exec tool");
      return t;
    };

    it("passes a default limit to the port, and the model's own limit when it asks for one", async () => {
      const { seen, manager } = recording(ok);
      await exec(manager).run({ command: "true" }, ctx);
      await exec(manager).run({ command: "true", timeout_seconds: 5 }, ctx);
      expect(seen).toEqual([120_000, 5_000]);
    });

    it("never lets the model ask for more than the maximum", async () => {
      const { seen, manager } = recording(ok);
      await exec(manager).run({ command: "true", timeout_seconds: 99_999 }, ctx);
      expect(seen).toEqual([600_000]);
    });

    it("turns a command the port stopped into a plain error observation", async () => {
      const { manager } = recording(async () => ({ stdout: "", stderr: "", exitCode: 124, timedOut: true }));
      await expect(exec(manager).run({ command: "sleep 999", timeout_seconds: 3 }, ctx)).rejects.toThrow(/did not finish within 3 seconds and was stopped/);
    });

    it("gives up on a port that never answers, a little after the limit", async () => {
      const { manager } = recording(() => new Promise(() => {}));
      const started = Date.now();
      await expect(exec(manager, { execTimeoutMs: 30, execGraceMs: 30 }).run({ command: "hang" }, ctx)).rejects.toThrow(/did not finish/);
      expect(Date.now() - started).toBeLessThan(2000);
    });
  });

  it("declares exec as an exec risk, write as write and read as read so the policy asks first", () => {
    const risks = Object.fromEntries(sandboxTools(new SandboxManager(() => new FakeSandbox()), "t").map((t) => [t.spec.name, t.spec.risk]));
    expect(risks).toEqual({ "sandbox.exec": "exec", "sandbox.read_file": "read", "sandbox.write_file": "write" });
  });
});

describe("outbound credential handler", () => {
  const rule = { host: "api.example.com", header: "authorization", secret: async () => "UPSTREAM-SECRET" };
  function setup() {
    const audits: Array<{ kind: string; data: Record<string, unknown> }> = [];
    const seen: Request[] = [];
    const handler = createOutboundHandler({
      rules: [rule],
      upstream: async (r) => {
        seen.push(r);
        return new Response("upstream ok");
      },
      audit: (kind, data) => void audits.push({ kind, data }),
    });
    return { handler, audits, seen };
  }

  it("denies and audits a request to an unlisted host", async () => {
    const { handler, audits, seen } = setup();
    const res = await handler(new Request("https://evil.example.org/steal"));
    expect(res.status).toBe(403);
    expect(seen).toHaveLength(0);
    expect(audits).toEqual([{ kind: "outbound.denied", data: { host: "evil.example.org", method: "GET" } }]);
  });

  it("adds the credential for a listed host and drops any credential the sandbox process supplied", async () => {
    const { handler, audits, seen } = setup();
    const res = await handler(
      new Request("https://api.example.com/v1/things?x=1", {
        headers: { authorization: "Bearer SANDBOX-MADE-THIS-UP", "x-api-key": "also-fake", cookie: "a=b", "x-keep": "yes" },
      }),
    );
    expect(await res.text()).toBe("upstream ok");
    const sent = seen[0] as Request;
    expect(sent.headers.get("authorization")).toBe("Bearer UPSTREAM-SECRET");
    expect(sent.headers.get("x-api-key")).toBeNull();
    expect(sent.headers.get("cookie")).toBeNull();
    expect(sent.headers.get("x-keep")).toBe("yes");
    expect(audits).toEqual([{ kind: "outbound.credentialed", data: { host: "api.example.com", method: "GET", path: "/v1/things" } }]);
    expect(JSON.stringify(audits)).not.toContain("UPSTREAM-SECRET");
  });

  it("refuses at construction to credential any model-provider host, so a sandbox request to one cannot be enabled", () => {
    for (const host of PROVIDER_HOSTS) {
      expect(() => createOutboundHandler({ rules: [{ ...rule, host }], upstream: async () => new Response(), audit: () => {} })).toThrow(OutboundConfigError);
    }
    expect(() => createOutboundHandler({ rules: [{ ...rule, host: "eu.api.openai.com" }], upstream: async () => new Response(), audit: () => {} })).toThrow(OutboundConfigError);
  });

  it("denies a sandbox request to the model provider host because it is not on the list, and audits it", async () => {
    const { handler, audits } = setup();
    const res = await handler(new Request("https://openrouter.ai/api/v1/chat/completions", { method: "POST", body: "{}" }));
    expect(res.status).toBe(403);
    expect(audits[0]).toMatchObject({ kind: "outbound.denied", data: { host: "openrouter.ai" } });
  });
});
