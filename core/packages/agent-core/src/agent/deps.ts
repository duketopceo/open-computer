import type { ApprovalRequester } from "../approvals/queue.ts";
import type { Policy } from "../approvals/policy.ts";
import type { CostGuard } from "../cost/guard.ts";
import type { PriceBook } from "../cost/pricebook.ts";
import type { EventBody } from "../events.ts";
import type { Provider } from "../provider/types.ts";
import type { ToolRegistry } from "./tools.ts";

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
}
