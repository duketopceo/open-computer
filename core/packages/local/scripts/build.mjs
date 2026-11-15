// Compiles the sidecar to one binary with bun and runs a smoke check on it: start it, read the ready line,
// call health with the token. Exits non-zero on any failure. Output goes to packages/local/out/.
import { spawn, spawnSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createInterface } from "node:readline";

const root = fileURLToPath(new URL("..", import.meta.url));
const out = `${root}out/oc-core`;
mkdirSync(`${root}out`, { recursive: true });

const bun = spawnSync("bun", ["--version"], { encoding: "utf8" });
if (bun.status !== 0) {
  console.error("bun is required to compile the sidecar (https://bun.sh).");
  process.exit(1);
}
const build = spawnSync("bun", ["build", "--compile", `${root}src/main.ts`, "--outfile", out], { stdio: "inherit" });
if (build.status !== 0) process.exit(build.status ?? 1);

const token = "smoke-token";
const child = spawn(out, [], { stdio: ["pipe", "pipe", "inherit"] });
const fail = (msg) => {
  console.error(`smoke check failed: ${msg}`);
  child.kill();
  process.exit(1);
};
const timer = setTimeout(() => fail("no ready line within 5 seconds"), 5000);
const rl = createInterface({ input: child.stdout });
rl.once("line", async (line) => {
  clearTimeout(timer);
  let ready;
  try {
    ready = JSON.parse(line);
  } catch {
    return fail("ready line was not JSON");
  }
  if (ready.event !== "ready" || typeof ready.port !== "number") return fail("unexpected ready line");
  const res = await fetch(`http://127.0.0.1:${ready.port}/v1/health`, { headers: { authorization: `Bearer ${token}` } });
  const body = await res.json();
  if (res.status !== 200 || body.ok !== true) return fail("health did not answer ok");
  child.stdin.end();
  console.log(`smoke check passed on port ${ready.port}`);
  setTimeout(() => process.exit(0), 200);
});
child.stdin.write(`${token}\n`);
