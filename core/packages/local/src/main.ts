// Sidecar entry: reads the bearer token from the first line of stdin, starts the loopback server and
// prints one JSON "ready" line with the port. Nothing else is written to stdout.
import { createInterface } from "node:readline";
import { startLocalServer, VERSION } from "./server.ts";

const rl = createInterface({ input: process.stdin });
const token = await new Promise<string>((resolve) => {
  rl.once("line", (line) => resolve(line.trim()));
  rl.once("close", () => resolve(""));
});
if (!token) {
  process.stderr.write("A token is required on the first line of stdin.\n");
  process.exit(2);
}
const running = await startLocalServer({ token });
process.stdout.write(`${JSON.stringify({ event: "ready", port: running.port, version: VERSION })}\n`);
// The shell closing stdin means it is gone; stop with it.
rl.on("close", () => void running.close().then(() => process.exit(0)));
process.on("SIGTERM", () => void running.close().then(() => process.exit(0)));
