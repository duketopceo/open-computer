import type { ApprovalRequester } from "../approvals/queue.ts";
import type { Policy } from "../approvals/policy.ts";
import type { CostGuard } from "../cost/guard.ts";
import type { PriceBook } from "../cost/pricebook.ts";
import type { EventBody } from "../events.ts";
import type { Provider } from "../provider/types.ts";
import type { ToolRegistry } from "./tools.ts";

export interface ToolOutcome {
  status: "ok" | "error";
  output: string;
  ms: number;
}

/**
 * Records that a tool call finished, keyed by task, step and call id, before anything else can fail. A retried
 * step reads it back instead of running the tool again, so a side effect happens once however often a step retries.
 */
export interface ToolJournal {
  get(key: string): Promise<ToolOutcome | undefined> | ToolOutcome | undefined;
  put(key: string, outcome: ToolOutcome): Promise<void> | void;
}

/** Everything the step function needs from its host. No runtime-specific API appears here. */
export interface AgentDeps {
  provider: Provider;
  tools: ToolRegistry;
  policy: Policy;
  approvals: ApprovalRequester;
  guard: CostGuard;
  prices: PriceBook;
  emit(body: EventBody): void;
  now(): number;
  signal?: AbortSignal;
  journal?: ToolJournal;
}
