import type { Env } from "../env.ts";
import type { SandboxPort } from "./port.ts";
import type { Sandbox } from "./sandbox-do.ts";

/** The agent-facing port over the Sandbox Durable Object for one task (a Container per task). */
export function cloudflareSandbox(env: Env, taskId: string): SandboxPort {
  if (!env.SANDBOX) throw new Error("The SANDBOX binding is not configured.");
  const stub = env.SANDBOX.getByName(taskId) as unknown as DurableObjectStub<Sandbox>;
  return {
    exec: (command, options) => stub.run(taskId, command, options?.cwd, options?.timeoutMs),
    readFile: (path) => stub.readText(taskId, path),
    writeFile: (path, content) => stub.writeText(taskId, path, content),
    destroy: () => stub.destroyContainer(),
    stopRecord: () => stub.lastStop(),
  };
}
