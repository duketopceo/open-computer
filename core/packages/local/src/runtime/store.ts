import { DatabaseSync } from "node:sqlite";
import {
  nextAuditRecord,
  type AgentEvent,
  type AuditRecord,
  type AuditStorage,
  type BlockName,
  type HistoryEntry,
  type LedgerPort,
  type MemoryStore,
  type ReserveResult,
} from "@open-computer/agent-core";

const SCHEMA = `
CREATE TABLE IF NOT EXISTS audit (seq INTEGER PRIMARY KEY, ts TEXT NOT NULL, task_id TEXT, kind TEXT NOT NULL, data TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS spend (step_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, day TEXT NOT NULL, amount REAL NOT NULL);
CREATE INDEX IF NOT EXISTS spend_task ON spend (task_id);
CREATE INDEX IF NOT EXISTS spend_day ON spend (day);
CREATE TABLE IF NOT EXISTS task_cap_extra (task_id TEXT PRIMARY KEY, amount REAL NOT NULL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks (task_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, goal TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, task_id TEXT NOT NULL, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS events_session ON events (session_id, id);
CREATE TABLE IF NOT EXISTS memory_blocks (name TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS memory_history (id TEXT PRIMARY KEY, task_id TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL, ts TEXT NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS memory_history_fts USING fts5(id UNINDEXED, title, summary);
`;

export function ftsQuery(text: string): string | null {
  const words = text
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter((w) => w.length >= 3);
  return words.length ? [...new Set(words)].map((w) => `"${w}"`).join(" OR ") : null;
}

type Row = Record<string, string | number | null>;

/** One SQLite file holds everything the daemon keeps: audit chain, spend, memory, settings, tasks and events. */
export class LocalStore {
  readonly db: DatabaseSync;
  private chain: Promise<unknown> = Promise.resolve();

  constructor(path: string) {
    this.db = new DatabaseSync(path);
    this.db.exec("PRAGMA journal_mode = WAL; PRAGMA busy_timeout = 5000;");
    this.db.exec(SCHEMA);
  }

  close(): void {
    this.db.close();
  }

  private all(sql: string, ...params: Array<string | number | null>): Row[] {
    return this.db.prepare(sql).all(...params) as Row[];
  }
  private one(sql: string, ...params: Array<string | number | null>): Row | undefined {
    return this.all(sql, ...params)[0];
  }
  private run(sql: string, ...params: Array<string | number | null>): void {
    this.db.prepare(sql).run(...params);
  }

  // ---- settings ----
  getSetting(key: string): string | null {
    return (this.one("SELECT value FROM settings WHERE key = ?", key)?.value as string | undefined) ?? null;
  }
  setSetting(key: string, value: string | null): void {
    if (value === null) this.run("DELETE FROM settings WHERE key = ?", key);
    else this.run("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", key, value);
  }

  // ---- audit ----
  readonly audit: AuditStorage = {
    appendNext: (make) => {
      const run = this.chain.then(async () => {
        const last = this.one("SELECT * FROM audit ORDER BY seq DESC LIMIT 1");
        const record = await make(last ? toAudit(last) : null);
        this.run(
          "INSERT INTO audit (seq, ts, task_id, kind, data, prev_hash, hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
          record.seq,
          record.ts,
          record.task_id,
          record.kind,
          JSON.stringify(record.data),
          record.prev_hash,
          record.hash,
        );
        return record;
      });
      this.chain = run.catch(() => undefined);
      return run;
    },
    read: async (fromSeq, limit) => this.all("SELECT * FROM audit WHERE seq >= ? ORDER BY seq ASC LIMIT ?", fromSeq, limit).map(toAudit),
  };

  /** Appends a note straight to the chain (used by diagnostics). */
  async appendAudit(kind: string, taskId: string | null, data: Record<string, unknown>): Promise<AuditRecord> {
    return this.audit.appendNext((last) => nextAuditRecord(last, { ts: new Date().toISOString(), taskId, kind, data }));
  }

  // ---- spend ----
  readonly ledger: LedgerPort = {
    tryReserve: async (i): Promise<ReserveResult> => {
      const taskSpent = Number(this.one("SELECT COALESCE(SUM(amount),0) AS s FROM spend WHERE task_id = ? AND step_id <> ?", i.taskId, i.stepId)?.s ?? 0);
      if (taskSpent + i.amount > i.taskCap + 1e-12) return { ok: false, scope: "task", spent: taskSpent, cap: i.taskCap };
      if (i.dayCap !== null) {
        const daySpent = Number(this.one("SELECT COALESCE(SUM(amount),0) AS s FROM spend WHERE day = ? AND step_id <> ?", i.day, i.stepId)?.s ?? 0);
        if (daySpent + i.amount > i.dayCap + 1e-12) return { ok: false, scope: "day", spent: daySpent, cap: i.dayCap };
      }
      this.run("INSERT INTO spend (step_id, task_id, day, amount) VALUES (?, ?, ?, ?) ON CONFLICT(step_id) DO UPDATE SET amount = excluded.amount", i.stepId, i.taskId, i.day, i.amount);
      return { ok: true };
    },
    settle: async ({ stepId, actual }) => {
      if (actual !== null) this.run("UPDATE spend SET amount = ? WHERE step_id = ?", actual, stepId);
    },
    taskSpent: async (t) => Number(this.one("SELECT COALESCE(SUM(amount),0) AS s FROM spend WHERE task_id = ?", t)?.s ?? 0),
    daySpent: async (d) => Number(this.one("SELECT COALESCE(SUM(amount),0) AS s FROM spend WHERE day = ?", d)?.s ?? 0),
    taskCapExtra: async (t) => Number(this.one("SELECT amount FROM task_cap_extra WHERE task_id = ?", t)?.amount ?? 0),
    addTaskCapExtra: async (t, by) => {
      this.run("INSERT INTO task_cap_extra (task_id, amount) VALUES (?, ?) ON CONFLICT(task_id) DO UPDATE SET amount = amount + excluded.amount", t, by);
    },
  };

  // ---- memory ----
  readonly memory: MemoryStore & { deleteHistory(id: string): Promise<void> } = {
    getBlock: async (name: BlockName) => (this.one("SELECT body FROM memory_blocks WHERE name = ?", name)?.body as string | undefined) ?? "",
    setBlock: async (name, text) => {
      this.run("INSERT INTO memory_blocks (name, body) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET body = excluded.body", name, text);
    },
    addHistory: async (e: HistoryEntry) => {
      this.run("DELETE FROM memory_history_fts WHERE id = ?", e.id);
      this.run("INSERT INTO memory_history (id, task_id, title, summary, ts) VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET task_id = excluded.task_id, title = excluded.title, summary = excluded.summary, ts = excluded.ts", e.id, e.taskId, e.title, e.summary, e.ts);
      this.run("INSERT INTO memory_history_fts (id, title, summary) VALUES (?, ?, ?)", e.id, e.title, e.summary);
    },
    listHistory: async (limit) => this.all("SELECT * FROM memory_history ORDER BY ts DESC LIMIT ?", limit).map(toHistory),
    searchHistory: async (query, limit) => {
      const q = ftsQuery(query);
      if (!q) return [];
      return this.all(
        "SELECT h.* FROM memory_history_fts f JOIN memory_history h ON h.id = f.id WHERE memory_history_fts MATCH ? ORDER BY rank LIMIT ?",
        q,
        limit,
      ).map(toHistory);
    },
    deleteHistory: async (id) => {
      this.run("DELETE FROM memory_history_fts WHERE id = ?", id);
      this.run("DELETE FROM memory_history WHERE id = ?", id);
    },
  };

  // ---- tasks and events ----
  insertTask(t: { taskId: string; sessionId: string; goal: string; createdAt?: string }): void {
    this.run("INSERT INTO tasks (task_id, session_id, goal, status, created_at) VALUES (?, ?, ?, 'running', ?)", t.taskId, t.sessionId, t.goal, t.createdAt ?? new Date().toISOString());
  }
  taskStatus(taskId: string): string | null {
    return (this.one("SELECT status FROM tasks WHERE task_id = ?", taskId)?.status as string | undefined) ?? null;
  }
  /** When the task last showed progress: its newest event, else when it was created. */
  lastProgressMs(taskId: string): number | null {
    const ev = this.one("SELECT data FROM events WHERE task_id = ? ORDER BY id DESC LIMIT 1", taskId);
    const fromEvent = ev ? Date.parse((JSON.parse(String(ev.data)) as { ts?: string }).ts ?? "") : Number.NaN;
    if (Number.isFinite(fromEvent)) return fromEvent;
    const created = Date.parse(String(this.one("SELECT created_at FROM tasks WHERE task_id = ?", taskId)?.created_at ?? ""));
    return Number.isFinite(created) ? created : null;
  }
  setTaskStatus(taskId: string, status: string): void {
    this.run("UPDATE tasks SET status = ? WHERE task_id = ?", status, taskId);
  }
  activeTaskInSession(sessionId: string): string | null {
    return (this.one("SELECT task_id FROM tasks WHERE session_id = ? AND status IN ('running','waiting') LIMIT 1", sessionId)?.task_id as string | undefined) ?? null;
  }
  /** Tasks left running by a process that died: they cannot resume, so they end as failed. */
  failOrphans(): number {
    const n = this.all("SELECT task_id FROM tasks WHERE status IN ('running','waiting')").length;
    this.run("UPDATE tasks SET status = 'failed' WHERE status IN ('running','waiting')");
    return n;
  }
  appendEvent(sessionId: string, event: AgentEvent): number {
    this.run("INSERT INTO events (session_id, task_id, data) VALUES (?, ?, ?)", sessionId, event.task_id, JSON.stringify(event));
    return Number(this.one("SELECT last_insert_rowid() AS id")?.id);
  }
  eventsAfter(sessionId: string, afterId: number): Array<{ id: number; event: AgentEvent }> {
    return this.all("SELECT id, data FROM events WHERE session_id = ? AND id > ? ORDER BY id", sessionId, afterId).map((r) => ({ id: Number(r.id), event: JSON.parse(String(r.data)) as AgentEvent }));
  }
}

const toAudit = (r: Row): AuditRecord => ({
  seq: Number(r.seq),
  ts: String(r.ts),
  task_id: (r.task_id as string | null) ?? null,
  kind: String(r.kind),
  data: JSON.parse(String(r.data)) as Record<string, unknown>,
  prev_hash: String(r.prev_hash),
  hash: String(r.hash),
});

const toHistory = (r: Row): HistoryEntry => ({ id: String(r.id), taskId: String(r.task_id), title: String(r.title), summary: String(r.summary), ts: String(r.ts) });
