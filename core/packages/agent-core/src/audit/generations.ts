import type { AuditRecord } from "./log.ts";

export interface RecentGeneration {
  id: string;
  task_id: string | null;
  ts: string;
}

/**
 * The OpenRouter generation ids recorded by model calls, newest first: at most `perTask` for each task and `total` overall.
 * Read from the audit log (its `event:usage` records carry `generation_id`), so every backend shares one source.
 */
export function recentGenerations(records: AuditRecord[], perTask = 5, total = 20): RecentGeneration[] {
  const seen = new Map<string, number>();
  const out: RecentGeneration[] = [];
  for (let i = records.length - 1; i >= 0 && out.length < total; i -= 1) {
    const r = records[i] as AuditRecord;
    if (r.kind !== "event:usage") continue;
    const id = (r.data as { generation_id?: unknown }).generation_id;
    if (typeof id !== "string" || id === "") continue;
    const key = r.task_id ?? "";
    const n = seen.get(key) ?? 0;
    if (n >= perTask) continue;
    seen.set(key, n + 1);
    out.push({ id, task_id: r.task_id, ts: r.ts });
  }
  return out;
}
