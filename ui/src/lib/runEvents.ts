// Run event stream: types, fetch with an `after` cursor, and the pure reducer.
// The wire contract is docs/design/event-stream.md (extended additively: goal ids `goal_<8 hex>`,
// event types `route` and `llm_call`, `tool_call` name `shell.exec`, optional `waiting_on` and `model`
// on `step`). Entities upsert by id: fields present replace, fields absent are kept, and first-seen
// order never changes. "Waiting" is a display state derived from `waiting_on`, not a status.

import { json } from "./api";

export type StepStatus = "pending" | "running" | "done" | "failed" | "skipped";
export type RunStatus = "queued" | "planning" | "running" | "waiting" | "paused" | "done" | "failed" | "cancelled";

export interface WaitingOn {
  kind: string;
  attempt?: number;
  max?: number;
  since?: number;
  detail?: string;
}

export interface Fallback {
  engine: string;
  kind: string;
  status?: number | null;
  reason?: string;
}

export interface RouteData {
  id: string;
  step: string;
  engine: string;
  model?: string;
  confidence?: number;
  ms?: number;
  fallbacks?: Fallback[];
}

export interface LlmCallData {
  id: string;
  step?: string;
  kind: "plan" | "work" | "decision" | "thread" | "chat";
  status?: "ok" | "error";
  /** the planner row (id call_plan) carries the plan JSON here */
  detail?: string;
  error_class?: string;
  http_status?: number;
  error?: string;
  model_requested?: string;
  model_served?: string;
  provider?: string;
  tokens_in?: number;
  tokens_out?: number;
  tokens_reasoning?: number;
  cost_usd?: number;
  latency_ms?: number;
  reasoning?: string;
}

export interface ToolCallData {
  id: string;
  name: string;
  status: "running" | "ok" | "error";
  step?: string;
  summary?: string;
  command?: string;
  rc?: number | null;
  stdout_tail?: string[];
  stderr_tail?: string[];
  duration_ms?: number;
}

export interface ErrorData {
  id: string;
  code: string;
  message: string;
  step?: string;
  hint?: string;
}

interface StepData {
  id: string;
  title: string;
  status: StepStatus;
  waiting_on?: WaitingOn | null;
  model?: string;
  detail?: string;
}

interface RunData {
  status: RunStatus;
  reason?: string;
  title?: string;
}

interface CostData {
  spent_usd: number;
  cap_usd?: number | null;
}

export type RunEvent = {
  id: string;
  v: number;
  goal: string;
  seq: number;
  ts: number;
  type: string;
  data: Record<string, unknown>;
};

export interface Step {
  id: string;
  title: string;
  status: StepStatus;
  waiting_on?: WaitingOn;
  model?: string;
  detail?: string;
  /** ts of the first event that said running; undefined while the step has not started */
  startedTs?: number;
  /** ts of the event that made the step terminal */
  endedTs?: number;
  firstTs: number;
  firstSeq: number;
}

export type StepDisplay = "pending" | "running" | "waiting" | "done" | "failed" | "skipped";

export function stepDisplay(s: Step): StepDisplay {
  if (s.status === "running" && s.waiting_on) return "waiting";
  return s.status;
}

export interface Timeline {
  lastSeq: number;
  run: (RunData & { startTs: number; endTs?: number }) | null;
  cost: CostData | null;
  /** steps in first-seen order */
  steps: Step[];
  stepIds: Record<string, number>;
  routes: Record<string, RouteData>;
  calls: Record<string, LlmCallData>;
  /** call ids per step id; the key "" holds calls with no step (plan, decision) */
  callsByStep: Record<string, string[]>;
  execs: Record<string, ToolCallData>;
  execsByStep: Record<string, string[]>;
  errors: Record<string, ErrorData>;
  errorsByStep: Record<string, string[]>;
}

export const emptyTimeline = (): Timeline => ({
  lastSeq: 0,
  run: null,
  cost: null,
  steps: [],
  stepIds: {},
  routes: {},
  calls: {},
  callsByStep: {},
  execs: {},
  execsByStep: {},
  errors: {},
  errorsByStep: {},
});

const TERMINAL: RunStatus[] = ["done", "failed", "cancelled"];
export const runTerminal = (t: Timeline) => !!t.run && TERMINAL.includes(t.run.status);

function upsertIndexed<T extends { id: string }>(
  items: Record<string, T>,
  byStep: Record<string, string[]>,
  d: T,
  step: string | undefined,
): void {
  const had = d.id in items;
  items[d.id] = { ...items[d.id], ...d };
  if (!had) byStep[step ?? ""] = [...(byStep[step ?? ""] ?? []), d.id];
}

/** Fold a batch of events into a new Timeline. Pure: the input is not mutated, and events at or
 *  below `lastSeq` are ignored, so a repeated poll is harmless. */
export function applyEvents(prev: Timeline, events: RunEvent[]): Timeline {
  const fresh = events.filter((e) => e.seq > prev.lastSeq);
  if (fresh.length === 0) return prev;
  const t: Timeline = {
    ...prev,
    steps: [...prev.steps],
    stepIds: { ...prev.stepIds },
    routes: { ...prev.routes },
    calls: { ...prev.calls },
    callsByStep: { ...prev.callsByStep },
    execs: { ...prev.execs },
    execsByStep: { ...prev.execsByStep },
    errors: { ...prev.errors },
    errorsByStep: { ...prev.errorsByStep },
  };
  for (const e of fresh) {
    t.lastSeq = Math.max(t.lastSeq, e.seq);
    const d = e.data as unknown;
    switch (e.type) {
      case "run": {
        const r = d as RunData;
        const startTs = t.run?.startTs ?? e.ts;
        const endTs = TERMINAL.includes(r.status) ? e.ts : undefined;
        t.run = { ...t.run, ...r, startTs, endTs };
        break;
      }
      case "cost":
        t.cost = { ...t.cost, ...(d as CostData) };
        break;
      case "step": {
        const s = d as StepData;
        const at = t.stepIds[s.id];
        const old = at === undefined ? undefined : t.steps[at];
        const next: Step = {
          ...(old ?? { id: s.id, title: s.id, status: "pending", firstTs: e.ts, firstSeq: e.seq }),
          ...stripUndefined({ title: s.title, status: s.status, model: s.model, detail: s.detail }),
          // not merged: the next step event without waiting_on clears it
          waiting_on: s.waiting_on ?? undefined,
        };
        if (next.status === "running" && next.startedTs === undefined) next.startedTs = e.ts;
        if (next.status === "done" || next.status === "failed") {
          next.endedTs = e.ts;
        } else if (next.status === "skipped") {
          next.endedTs = e.ts;
          next.startedTs = undefined; // a skipped step never ran: no duration
        }
        if (at === undefined) {
          t.stepIds[s.id] = t.steps.length;
          t.steps.push(next);
        } else {
          t.steps[at] = next;
        }
        break;
      }
      case "route": {
        const r = d as RouteData;
        t.routes[r.step] = { ...t.routes[r.step], ...r };
        break;
      }
      case "llm_call": {
        const c = d as LlmCallData;
        upsertIndexed(t.calls, t.callsByStep, c, c.step);
        break;
      }
      case "tool_call": {
        const c = d as ToolCallData;
        upsertIndexed(t.execs, t.execsByStep, c, c.step);
        break;
      }
      case "error": {
        const c = d as ErrorData;
        upsertIndexed(t.errors, t.errorsByStep, c, c.step);
        break;
      }
      default:
        break; // other goal-stream types (note, source, ...) are not part of this view
    }
  }
  return t;
}

function stripUndefined<T extends object>(o: T): Partial<T> {
  return Object.fromEntries(Object.entries(o).filter(([, v]) => v !== undefined)) as Partial<T>;
}

// ---- fetch

export const EVENTS_PAGE = 500; // the API's per-call cap (KTD10)

export interface EventsReply {
  events: RunEvent[];
  last_seq: number;
  finished: boolean;
}

export const getRunEvents = (run: string, after: number) =>
  json<EventsReply>(`/api/runs/${encodeURIComponent(run)}/events?after=${after}`);

export const exportUrl = (run: string) => `/api/runs/${encodeURIComponent(run)}/events.jsonl`;

/** True when the run has a stream. The cursor is past any real seq (the API clamps it), so the
 *  probe returns no events: 200 means a stream exists, 404 means dev trace was off. */
export async function hasStream(run: string): Promise<boolean> {
  try {
    await getRunEvents(run, 2 ** 31 - 1);
    return true;
  } catch {
    return false;
  }
}
