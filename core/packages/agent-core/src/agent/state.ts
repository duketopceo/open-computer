import type { ApprovalKind, ApprovalOutcome } from "../approvals/queue.ts";
import type { RunStatus } from "../events.ts";
import type { ChatMessage, ToolCall } from "../provider/types.ts";

export interface AgentLimits {
  maxSteps: number;
  maxConsecutiveToolFailures: number;
  wallClockMs: number;
}

export const DEFAULT_LIMITS: AgentLimits = { maxSteps: 25, maxConsecutiveToolFailures: 3, wallClockMs: 15 * 60 * 1000 };

export interface PendingApproval {
  approvalId: string;
  kind: ApprovalKind;
  hash: string;
  /** For a tool approval: the call being gated. For a cap raise: the dollars to add. */
  call?: ToolCall;
  amount?: number;
  /** When the approval was requested; the wait is not counted against the wall-clock limit. */
  requestedAtMs: number;
  /** Set by `resolveApproval` once a human decided. */
  outcome?: ApprovalOutcome;
}

export type TaskStatus = RunStatus;

export function isTerminal(status: string): status is "done" | "failed" | "cancelled" {
  return status === "done" || status === "failed" || status === "cancelled";
}

/** Plain data only, so a durable driver can persist it and resume in another process. */
export interface AgentState {
  taskId: string;
  model: string;
  sandboxId: string;
  messages: ChatMessage[];
  stepCount: number;
  consecutiveToolFailures: number;
  startedAtMs: number;
  limits: AgentLimits;
  status: TaskStatus;
  /** Tool calls from the latest assistant message still to execute, in call order. */
  remainingCalls: ToolCall[];
  pending?: PendingApproval;
  failureReason?: string;
}

export interface CreateStateInput {
  taskId: string;
  model: string;
  system?: string;
  goal: string;
  sandboxId?: string;
  startedAtMs: number;
  limits?: Partial<AgentLimits>;
}

export function createState(input: CreateStateInput): AgentState {
  const messages: ChatMessage[] = [];
  if (input.system) messages.push({ role: "system", content: input.system });
  messages.push({ role: "user", content: input.goal });
  return {
    taskId: input.taskId,
    model: input.model,
    sandboxId: input.sandboxId ?? input.taskId,
    messages,
    stepCount: 0,
    consecutiveToolFailures: 0,
    startedAtMs: input.startedAtMs,
    limits: { ...DEFAULT_LIMITS, ...input.limits },
    status: "running",
    remainingCalls: [],
  };
}

/** Records the human decision on the pending approval so the next step can act on it. */
export function resolveApproval(state: AgentState, outcome: ApprovalOutcome): AgentState {
  if (!state.pending) return state;
  return { ...state, status: "running", pending: { ...state.pending, outcome } };
}
