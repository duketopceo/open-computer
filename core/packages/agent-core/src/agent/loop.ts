import { EventSequencer, type AgentEvent, type EventBody } from "../events.ts";
import type { AgentDeps } from "./deps.ts";
import { isTerminal, resolveApproval, type AgentState } from "./state.ts";
import { step } from "./step.ts";

export interface RunLoopOptions {
  /** Receives every stamped event (audit, SSE fan-out, tests). */
  onEvent?: (event: AgentEvent) => void | Promise<void>;
  sequencer?: EventSequencer;
}

export interface RunLoopResult {
  state: AgentState;
  events: AgentEvent[];
}

/**
 * In-process driver: step until the task ends, waiting on the approval queue whenever a step pauses.
 * The cloud driver (a Workflow) calls `step` itself and waits on workflow events instead.
 */
export async function runLoop(
  initial: AgentState,
  deps: Omit<AgentDeps, "emit">,
  options: RunLoopOptions = {},
): Promise<RunLoopResult> {
  const sequencer = options.sequencer ?? new EventSequencer(initial.taskId, 0, () => new Date(deps.now()).toISOString());
  const events: AgentEvent[] = [];
  const pendingWrites: Array<void | Promise<void>> = [];
  const emit = (body: EventBody): void => {
    const event = sequencer.stamp(body);
    events.push(event);
    pendingWrites.push(options.onEvent?.(event));
  };
  const full: AgentDeps = { ...deps, emit };

  let state = initial;
  emit({ type: "run", status: "running" });
  for (;;) {
    const outcome = await step(state, full);
    state = outcome.state;
    await Promise.all(pendingWrites.splice(0));
    if (isTerminal(outcome.kind)) break;
    if (outcome.kind === "awaiting_approval") {
      const pending = state.pending;
      if (!pending) break;
      const result = await deps.approvals.wait(pending.approvalId);
      const rec = deps.approvals.get(pending.approvalId);
      emit({
        type: "approval",
        approval_id: pending.approvalId,
        kind: pending.kind,
        state: result,
        hash: pending.hash,
        summary: rec?.summary ?? "",
      });
      state = resolveApproval(state, result);
      emit({ type: "run", status: "running" });
    }
  }
  await Promise.all(pendingWrites.splice(0));
  return { state, events };
}
