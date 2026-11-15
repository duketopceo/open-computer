import type { Redactor } from "../audit/redact.ts";
import type { MemoryStore } from "./store.ts";

const SUMMARY_LIMIT = 800;

export interface TaskSummaryInput {
  taskId: string;
  goal: string;
  outcome: string;
  ts: string;
}

/** Tier 2: one redacted summary per finished task, keyed so a re-run replaces rather than duplicates. */
export async function recordTaskSummary(store: MemoryStore, redactor: Redactor, input: TaskSummaryInput): Promise<string> {
  const title = redactor.redact(input.goal.trim()).slice(0, 80);
  const summary = redactor.redact(`Goal: ${input.goal.trim()}\nOutcome: ${input.outcome.trim()}`).slice(0, SUMMARY_LIMIT);
  const id = `task:${input.taskId}`;
  await store.addHistory({ id, taskId: input.taskId, title, summary, ts: input.ts });
  return id;
}
