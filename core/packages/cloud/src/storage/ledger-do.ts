import { DurableObject } from "cloudflare:workers";
import { createRedactor, nextAuditRecord, type AuditRecord, type ReserveResult } from "@open-computer/agent-core";
import type { Env } from "../env.ts";

interface AuditRow {
  seq: number;
  ts: string;
  task_id: string | null;
  kind: string;
  data: string;
  prev_hash: string;
  hash: string;
}

const AUTH_WINDOW_MS = 60_000;
const AUTH_MAX_FAILURES = 10;

const toRecord = (r: AuditRow): AuditRecord => ({
  seq: r.seq,
  ts: r.ts,
  task_id: r.task_id,
  kind: r.kind,
  data: JSON.parse(r.data) as Record<string, unknown>,
  prev_hash: r.prev_hash,
  hash: r.hash,
});

/**
 * The only writer of audit appends and spend reservations. D1 has no interactive transactions, so every
 * read-then-write runs through this object's mutex; D1 holds the rows (unique sequence number as a backstop).
 * One account-wide instance: always address it by the name "ledger".
 */
export class LedgerDO extends DurableObject<Env> {
  private chain: Promise<unknown> = Promise.resolve();
  private readonly redactor = createRedactor();

  private serialize<T>(fn: () => Promise<T>): Promise<T> {
    const run = this.chain.then(fn);
    this.chain = run.catch(() => undefined);
    return run;
  }

  // ---- audit ----

  appendAudit(kind: string, taskId: string | null, data: Record<string, unknown>): Promise<AuditRecord> {
    return this.serialize(async () => {
      const last = await this.env.DB.prepare("SELECT * FROM audit ORDER BY seq DESC LIMIT 1").first<AuditRow>();
      const record = await nextAuditRecord(last ? toRecord(last) : null, {
        ts: new Date().toISOString(),
        taskId,
        kind,
        data: this.redactor.redact(data),
      });
      await this.env.DB.prepare(
        "INSERT INTO audit (seq, ts, task_id, kind, data, prev_hash, hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
      )
        .bind(record.seq, record.ts, record.task_id, record.kind, JSON.stringify(record.data), record.prev_hash, record.hash)
        .run();
      return record;
    });
  }

  async readAudit(fromSeq: number, limit: number): Promise<AuditRecord[]> {
    const { results } = await this.env.DB.prepare("SELECT * FROM audit WHERE seq >= ? ORDER BY seq ASC LIMIT ?")
      .bind(fromSeq, limit)
      .all<AuditRow>();
    return results.map(toRecord);
  }

  // ---- spend ----

  tryReserve(i: {
    taskId: string;
    stepId: string;
    day: string;
    amount: number;
    taskCap: number;
    dayCap: number | null;
  }): Promise<ReserveResult> {
    return this.serialize(async () => {
      const db = this.env.DB;
      const task = await db
        .prepare("SELECT COALESCE(SUM(amount), 0) AS s FROM spend WHERE task_id = ? AND step_id <> ?")
        .bind(i.taskId, i.stepId)
        .first<{ s: number }>();
      const taskSpent = task?.s ?? 0;
      if (taskSpent + i.amount > i.taskCap + 1e-12) return { ok: false, scope: "task", spent: taskSpent, cap: i.taskCap };
      if (i.dayCap !== null) {
        const day = await db
          .prepare("SELECT COALESCE(SUM(amount), 0) AS s FROM spend WHERE day = ? AND step_id <> ?")
          .bind(i.day, i.stepId)
          .first<{ s: number }>();
        const daySpent = day?.s ?? 0;
        if (daySpent + i.amount > i.dayCap + 1e-12) return { ok: false, scope: "day", spent: daySpent, cap: i.dayCap };
      }
      await db
        .prepare(
          "INSERT INTO spend (step_id, task_id, day, amount) VALUES (?, ?, ?, ?) ON CONFLICT(step_id) DO UPDATE SET amount = excluded.amount",
        )
        .bind(i.stepId, i.taskId, i.day, i.amount)
        .run();
      return { ok: true };
    });
  }

  settle(i: { stepId: string; actual: number | null }): Promise<void> {
    return this.serialize(async () => {
      if (i.actual === null) return;
      await this.env.DB.prepare("UPDATE spend SET amount = ? WHERE step_id = ?").bind(i.actual, i.stepId).run();
    });
  }

  async taskSpent(taskId: string): Promise<number> {
    const r = await this.env.DB.prepare("SELECT COALESCE(SUM(amount), 0) AS s FROM spend WHERE task_id = ?").bind(taskId).first<{ s: number }>();
    return r?.s ?? 0;
  }

  async daySpent(day: string): Promise<number> {
    const r = await this.env.DB.prepare("SELECT COALESCE(SUM(amount), 0) AS s FROM spend WHERE day = ?").bind(day).first<{ s: number }>();
    return r?.s ?? 0;
  }

  async taskCapExtra(taskId: string): Promise<number> {
    const r = await this.env.DB.prepare("SELECT amount FROM task_cap_extra WHERE task_id = ?").bind(taskId).first<{ amount: number }>();
    return r?.amount ?? 0;
  }

  addTaskCapExtra(taskId: string, by: number): Promise<void> {
    return this.serialize(async () => {
      await this.env.DB.prepare(
        "INSERT INTO task_cap_extra (task_id, amount) VALUES (?, ?) ON CONFLICT(task_id) DO UPDATE SET amount = amount + excluded.amount",
      )
        .bind(taskId, by)
        .run();
    });
  }

  // ---- auth throttle (repeated wrong tokens from one source) ----

  async authBlocked(source: string, now = Date.now()): Promise<boolean> {
    const rec = await this.ctx.storage.get<{ count: number; since: number }>(`auth:${source}`);
    return !!rec && now - rec.since < AUTH_WINDOW_MS && rec.count >= AUTH_MAX_FAILURES;
  }

  async authFailed(source: string, now = Date.now()): Promise<void> {
    const rec = await this.ctx.storage.get<{ count: number; since: number }>(`auth:${source}`);
    const fresh = !rec || now - rec.since >= AUTH_WINDOW_MS;
    await this.ctx.storage.put(`auth:${source}`, fresh ? { count: 1, since: now } : { count: rec.count + 1, since: rec.since });
  }
}
