import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readdirSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { bubblewrapFactory, bwrapArgs, e2bFactory, inside, sandboxFactoryFor, stubFactory, type E2bSandboxLike } from "../src/runtime/sandbox.ts";

/** True when bubblewrap exists and this machine lets it create the namespaces it needs. */
function bwrapWorks(): boolean {
  const r = spawnSync("bwrap", ["--unshare-all", "--ro-bind", "/usr", "/usr", "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64", "true"], { encoding: "utf8" });
  return r.status === 0;
}
const real = bwrapWorks();

describe("path confinement", () => {
  it("keeps paths inside the workspace", () => {
    const root = "/tmp/ws";
    expect(inside(root, "a/b.txt")).toBe("/tmp/ws/a/b.txt");
    expect(inside(root, "/workspace/a.txt")).toBe("/tmp/ws/a.txt");
    expect(() => inside(root, "../etc/passwd")).toThrow(/outside/);
    expect(() => inside(root, "/etc/passwd")).toThrow(/outside/);
  });
});

describe("bubblewrap arguments", () => {
  it("unshare everything, clear the environment and pass only the allowlist", () => {
    const args = bwrapArgs("/tmp/ws", "echo hi");
    expect(args).toContain("--unshare-all");
    expect(args).toContain("--clearenv");
    expect(args.slice(-3)).toEqual(["sh", "-c", "echo hi"]);
    const setenv = args.map((a, i) => (a === "--setenv" ? args[i + 1] : null)).filter(Boolean);
    expect(setenv.every((n) => !/key|token|secret|password/i.test(String(n)))).toBe(true);
    expect(args).not.toContain("--share-net");
  });
});

describe.skipIf(!real)("bubblewrap sandbox (real)", () => {
  const make = async () => {
    const root = mkdtempSync(path.join(tmpdir(), "oc-bw-test-"));
    const box = await bubblewrapFactory({ root })("task-12345678");
    return { box, root };
  };

  it("runs a command, returns output and exit codes, and cleans up on destroy", async () => {
    const { box, root } = await make();
    expect((await box.exec("echo hello")).stdout).toBe("hello\n");
    const bad = await box.exec("exit 3");
    expect(bad.exitCode).toBe(3);
    expect(readdirSync(root)).toHaveLength(1);
    await box.destroy();
    expect(readdirSync(root)).toHaveLength(0);
  });

  it("has no network and no host secrets in its environment", async () => {
    const { box } = await make();
    process.env.OC_TEST_SECRET_KEY = "should-not-leak";
    try {
      const env = (await box.exec("env")).stdout;
      expect(env).not.toContain("should-not-leak");
      expect(env).not.toMatch(/OC_TEST_SECRET_KEY/);
      const net = await box.exec("cat /proc/net/dev | tail -n +3 | grep -v '^ *lo:' | wc -l");
      expect(net.stdout.trim()).toBe("0");
    } finally {
      delete process.env.OC_TEST_SECRET_KEY;
      await box.destroy();
    }
  });

  it("cannot see the host home directory", async () => {
    const { box } = await make();
    expect((await box.exec("test -e /root/.ssh && echo yes || echo no")).stdout.trim()).toBe("no");
    await box.destroy();
  });

  it("writes and reads files in the workspace and refuses paths that escape it", async () => {
    const { box } = await make();
    await box.writeFile("notes/a.txt", "hello file");
    expect(await box.readFile("notes/a.txt")).toBe("hello file");
    expect((await box.exec("cat /workspace/notes/a.txt")).stdout).toBe("hello file");
    await expect(box.readFile("../../etc/passwd")).rejects.toThrow(/outside/);
    await expect(box.writeFile("/etc/x", "no")).rejects.toThrow(/outside/);
    await box.destroy();
  });

  it("kills a command that runs past its limit", async () => {
    const { box } = await make();
    const r = await box.exec("sleep 5", { timeoutMs: 300 });
    expect(r.timedOut).toBe(true);
    expect(r.exitCode).toBe(124);
    await box.destroy();
  });

  it("gives each task its own directory", async () => {
    const root = mkdtempSync(path.join(tmpdir(), "oc-bw-test-"));
    const f = bubblewrapFactory({ root });
    const a = await f("aaaaaaaa-1");
    const b = await f("bbbbbbbb-2");
    await a.writeFile("only-a.txt", "a");
    await expect(b.readFile("only-a.txt")).rejects.toThrow();
    await a.destroy();
    await b.destroy();
    expect(existsSync(root)).toBe(true);
  });
});

describe("E2B-compatible adapter (SPACE and Cube)", () => {
  function fakeSdk() {
    const log: string[] = [];
    const sdk: E2bSandboxLike = {
      commands: {
        run: async (cmd) => {
          log.push(`run ${cmd}`);
          if (cmd === "fail") throw Object.assign(new Error("exit"), { exitCode: 2, stdout: "o", stderr: "e" });
          if (cmd === "slow") throw Object.assign(new Error("t"), { name: "TimeoutError" });
          if (cmd === "boom") throw new Error("connection lost");
          return { exitCode: 0, stdout: "ok", stderr: "" };
        },
      },
      files: { read: async (p) => `read ${p}`, write: async (p) => void log.push(`write ${p}`) },
      kill: async () => void log.push("kill"),
    };
    return { sdk, log };
  }

  it("treats a non-zero exit as a result, a timeout as 124, and a lost connection as an error", async () => {
    const { sdk, log } = fakeSdk();
    const box = await e2bFactory({ apiUrl: "http://x", apiKey: "k", template: "t" }, async () => sdk)("t1");
    expect(await box.exec("echo")).toMatchObject({ exitCode: 0, stdout: "ok" });
    expect(await box.exec("fail")).toMatchObject({ exitCode: 2, stdout: "o", stderr: "e" });
    expect(await box.exec("slow")).toMatchObject({ exitCode: 124, timedOut: true });
    await expect(box.exec("boom")).rejects.toThrow(/connection lost/);
    expect(await box.readFile("/a")).toBe("read /a");
    await box.writeFile("/b", "x");
    await box.destroy();
    expect(log.at(-1)).toBe("kill");
  });

  it("passes the SPACE configuration through and refuses plainly when it is incomplete", async () => {
    let seen: unknown;
    const f = sandboxFactoryFor({
      kind: "space",
      env: { SPACE_API_URL: "http://127.0.0.1:8080", SPACE_API_KEY: "k", SPACE_TEMPLATE_ID: "seed", SPACE_DOMAIN: "local.e2b.test", SPACE_DATA_PLANE_URL: "http://127.0.0.1:8092" },
      create: async (c) => ((seen = c), fakeSdk().sdk),
    });
    await f("t");
    expect(seen).toEqual({ apiUrl: "http://127.0.0.1:8080", apiKey: "k", template: "seed", domain: "local.e2b.test", sandboxUrl: "http://127.0.0.1:8092" });
    await expect(sandboxFactoryFor({ kind: "cube", env: {} })("t")).rejects.toThrow(/CUBE_API_URL/);
    await expect(sandboxFactoryFor({ kind: "none", env: {} })("t")).rejects.toThrow(/No sandbox/);
  });
});

describe("stub sandbox (CI only)", () => {
  it("answers only the diagnostics commands and refuses everything else plainly", async () => {
    const box = await stubFactory()("t");
    expect((await box.exec("echo oc-sandbox-ok")).stdout).toBe("oc-sandbox-ok\n");
    expect((await box.exec("env")).stdout).not.toMatch(/key|token|secret/i);
    expect((await box.exec("curl https://example.com")).stdout).toContain("000");
    expect(await box.exec("rm -rf /")).toMatchObject({ exitCode: 126 });
    await expect(box.readFile("a")).rejects.toThrow(/no files/);
    expect(await sandboxFactoryFor({ kind: "stub", env: {} })("t")).toBeDefined();
  });
});
