import { z } from "zod";

/**
 * The one event stream every core component emits and every client reads.
 * Type names follow `oc/events` v1 plus the additive v1.1 streaming types described in
 * docs/plans/2026-11-15-unified-runtime-plan.md section 8.
 */

export const EVENT_TYPE_NAMES = [
  "run",
  "step",
  "message.delta",
  "message.done",
  "reasoning.delta",
  "reasoning.done",
  "tool_call.started",
  "tool_call.args.delta",
  "tool_call.finished",
  "tool_call.result",
  "usage",
  "cost",
  "approval",
  "error",
  "memory_recall",
  "memory_write",
  "note",
] as const;

const base = {
  task_id: z.string().min(1),
  seq: z.number().int().nonnegative(),
  ts: z.string().min(1),
};

const step = z.number().int().nonnegative();

export const RUN_STATUSES = ["running", "waiting", "done", "failed", "cancelled"] as const;
export const APPROVAL_KINDS = ["tool", "cap_raise"] as const;
export const APPROVAL_STATES = ["requested", "approved", "denied", "expired"] as const;
export const RISK_CLASSES = ["read", "write", "network", "exec"] as const;

export const agentEventSchema = z.discriminatedUnion("type", [
  z.object({
    ...base,
    type: z.literal("run"),
    status: z.enum(RUN_STATUSES),
    reason: z.string().optional(),
  }),
  z.object({ ...base, type: z.literal("step"), step, state: z.enum(["start", "end"]) }),
  z.object({ ...base, type: z.literal("message.delta"), step, text: z.string() }),
  z.object({ ...base, type: z.literal("message.done"), step, text: z.string() }),
  z.object({
    ...base,
    type: z.literal("reasoning.delta"),
    step,
    kind: z.enum(["text", "summary"]),
    text: z.string(),
  }),
  z.object({
    ...base,
    type: z.literal("reasoning.done"),
    step,
    kind: z.enum(["text", "summary"]),
    tokens: z.number().int().nonnegative().nullable(),
  }),
  z.object({
    ...base,
    type: z.literal("tool_call.started"),
    step,
    call_id: z.string(),
    name: z.string(),
    index: z.number().int().nonnegative(),
  }),
  z.object({ ...base, type: z.literal("tool_call.args.delta"), call_id: z.string(), text: z.string() }),
  z.object({
    ...base,
    type: z.literal("tool_call.finished"),
    call_id: z.string(),
    name: z.string(),
    args: z.string(),
    risk: z.enum(RISK_CLASSES),
  }),
  z.object({
    ...base,
    type: z.literal("tool_call.result"),
    call_id: z.string(),
    status: z.enum(["ok", "error", "denied"]),
    output: z.string(),
    ms: z.number().nonnegative(),
  }),
  z.object({
    ...base,
    type: z.literal("usage"),
    step,
    prompt: z.number().int().nonnegative(),
    completion: z.number().int().nonnegative(),
    reasoning: z.number().int().nonnegative(),
    cached: z.number().int().nonnegative(),
    cost: z.number().nonnegative().nullable(),
    estimated: z.boolean(),
    /** OpenRouter's id for this model call (`gen-...`), when the provider sent one. */
    generation_id: z.string().min(1).optional(),
  }),
  z.object({
    ...base,
    type: z.literal("cost"),
    task_total: z.number().nonnegative(),
    day_total: z.number().nonnegative(),
    task_cap: z.number().nonnegative(),
    day_cap: z.number().nonnegative().nullable(),
  }),
  z.object({
    ...base,
    type: z.literal("approval"),
    approval_id: z.string(),
    kind: z.enum(APPROVAL_KINDS),
    state: z.enum(APPROVAL_STATES),
    hash: z.string(),
    summary: z.string(),
  }),
  z.object({
    ...base,
    type: z.literal("error"),
    code: z.string(),
    message: z.string(),
    step: step.optional(),
    retryable: z.boolean().optional(),
  }),
  z.object({
    ...base,
    type: z.literal("memory_recall"),
    items: z.array(z.object({ id: z.string(), title: z.string(), source: z.string() })),
  }),
  z.object({
    ...base,
    type: z.literal("memory_write"),
    id: z.string(),
    tier: z.enum(["core", "history"]),
  }),
  z.object({ ...base, type: z.literal("note"), text: z.string() }),
]);

export type AgentEvent = z.infer<typeof agentEventSchema>;
export type AgentEventType = AgentEvent["type"];
export type RunStatus = (typeof RUN_STATUSES)[number];
export type RiskClass = (typeof RISK_CLASSES)[number];

/** Distributes Omit over the union so each event body keeps its own fields. */
export type EventBody = AgentEvent extends infer E
  ? E extends AgentEvent
    ? Omit<E, "task_id" | "seq" | "ts">
    : never
  : never;

export function parseEvent(input: unknown): AgentEvent {
  return agentEventSchema.parse(input);
}

export function serializeEvent(event: AgentEvent): string {
  return JSON.stringify(agentEventSchema.parse(event));
}

/** Stamps bodies with task id, a monotonic sequence number and a timestamp. */
export class EventSequencer {
  private next: number;
  private readonly taskId: string;
  private readonly clock: () => string;
  constructor(taskId: string, start = 0, clock: () => string = () => new Date().toISOString()) {
    this.taskId = taskId;
    this.next = start;
    this.clock = clock;
  }
  stamp(body: EventBody): AgentEvent {
    const event = { ...body, task_id: this.taskId, seq: this.next, ts: this.clock() } as AgentEvent;
    this.next += 1;
    return event;
  }
  get nextSeq(): number {
    return this.next;
  }
}

export function eventsJsonSchema(): unknown {
  return z.toJSONSchema(agentEventSchema, { target: "draft-2020-12" });
}
