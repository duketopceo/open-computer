// The sandbox's main process must exit 0 when Cloudflare ends the instance with SIGTERM (a clean stop, not a kill).
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { test } from "node:test";
import { MAIN_PROCESS } from "../src/sandbox/main-process.ts";

const run = (signal) =>
  new Promise((resolve, reject) => {
    const child = spawn(MAIN_PROCESS[0], MAIN_PROCESS.slice(1), { stdio: "ignore" });
    child.on("error", reject);
    child.on("exit", (code, sig) => resolve({ code, sig }));
    // Give the shell a moment to install its trap, then end it the way the platform does.
    setTimeout(() => child.kill(signal), 300);
    setTimeout(() => child.kill("SIGKILL"), 5000).unref();
  });

test("the sandbox main process exits with code 0 on SIGTERM", async () => {
  assert.deepEqual(await run("SIGTERM"), { code: 0, sig: null });
});

test("and on SIGINT", async () => {
  assert.deepEqual(await run("SIGINT"), { code: 0, sig: null });
});

test("a plain `sleep infinity` main process would not be a clean stop (why the trap is there)", async () => {
  const code = await new Promise((resolve) => {
    const child = spawn("sleep", ["infinity"], { stdio: "ignore" });
    child.on("exit", (c, sig) => resolve(sig ?? c));
    setTimeout(() => child.kill("SIGTERM"), 200);
  });
  // Outside a container `sleep` dies of SIGTERM (as process 1 it would ignore it and be killed later); neither is exit code 0.
  assert.notEqual(code, 0);
});
