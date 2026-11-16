// Production entry. Exports the Worker plus every Durable Object, Workflow and Sandbox class named in wrangler.jsonc.
import { createHandler } from "./handler.ts";

export { SessionDO } from "./session-do.ts";
export { LedgerDO } from "./storage/ledger-do.ts";
export { TaskWorkflow } from "./task-workflow.ts";
export { Sandbox, OutboundProxy } from "./sandbox/sandbox-do.ts";

export default createHandler({ fetch: (url, init) => fetch(url, init) });
