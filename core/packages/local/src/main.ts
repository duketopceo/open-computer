// Sidecar entry: reads the bearer token from the first line of stdin, starts the loopback server and
// prints one JSON "ready" line with the port. Nothing else is written to stdout.
import { createInterface } from "node:readline";
import { readFileSync } from "node:fs";
import { parseDaemonOptions } from "./daemon.ts";
import { runtimeFromEnv } from "./runtime/boot.ts";
import { startLocalServer, VERSION } from "./server.ts";

// Daemon mode (self-hosted server): `oc-core --daemon` reads the token from OC_CORE_TOKEN_FILE and the port from
// OC_CORE_PORT, stays up until SIGTERM, and still binds loopback only; a tunnel on the same host exposes it.
const daemon = parseDaemonOptions(process.argv.slice(2), process.env);
if (daemon) {
  const token = readFileSync(daemon.tokenFile, "utf8").trim();
  if (!token) {
    process.stderr.write("The token file is empty.\n");
    process.exit(2);
  }
  const running = await startLocalServer({ token, port: daemon.port, runtime: runtimeFromEnv(process.env) });
  process.stdout.write(`${JSON.stringify({ event: "ready", port: running.port, version: VERSION })}\n`);
  process.on("SIGTERM", () => void running.close().then(() => process.exit(0)));
  process.on("SIGINT", () => void running.close().then(() => process.exit(0)));
  await new Promise(() => {});
}

const rl = createInterface({ input: process.stdin });
const token = await new Promise<string>((resolve) => {
  rl.once("line", (line) => resolve(line.trim()));
  rl.once("close", () => resolve(""));
});
if (!token) {
  process.stderr.write("A token is required on the first line of stdin.\n");
  process.exit(2);
}
const running = await startLocalServer({ token, ...(process.env.OC_LOCAL_RUNTIME === "0" ? {} : { runtime: runtimeFromEnv(process.env) }) });
process.stdout.write(`${JSON.stringify({ event: "ready", port: running.port, version: VERSION })}\n`);
// The shell closing stdin means it is gone; stop with it.
rl.on("close", () => void running.close().then(() => process.exit(0)));
process.on("SIGTERM", () => void running.close().then(() => process.exit(0)));
