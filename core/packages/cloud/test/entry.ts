// Test entry for the Workers test pool. It builds the same Worker and Durable Objects as production, with the
// fake provider, fake sandbox and fake outbound fetch entering through the three overridable hooks. Production
// code has no flag that selects a fake.
import type { FetchLike, Provider } from "@open-computer/agent-core";
import { createHandler } from "../src/handler.ts";
import type { SandboxFactory } from "../src/sandbox/port.ts";
import { SessionDO as BaseSessionDO } from "../src/session-do.ts";

export { LedgerDO } from "../src/storage/ledger-do.ts";
export { TaskWorkflow } from "../src/task-workflow.ts";

export interface Doubles {
  provider: Provider;
  sandbox: SandboxFactory;
  fetch: FetchLike;
}

const g = globalThis as unknown as { __doubles?: Doubles };

export function setDoubles(d: Doubles): void {
  g.__doubles = d;
}
const doubles = (): Doubles => {
  if (!g.__doubles) throw new Error("test doubles were not set");
  return g.__doubles;
};

export class SessionDO extends BaseSessionDO {
  protected override makeProvider(): Provider {
    return (request, signal) => doubles().provider(request, signal);
  }
  protected override makeSandboxFactory(): SandboxFactory {
    return (taskId) => doubles().sandbox(taskId);
  }
}

export default createHandler({ fetch: (url, init) => doubles().fetch(url, init) });
