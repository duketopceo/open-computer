import type { BlockName, HistoryEntry, MemoryStore } from "@open-computer/agent-core";

interface HistoryRow {
  id: string;
  task_id: string;
  title: string;
  summary: string;
  ts: string;
}

const toEntry = (r: HistoryRow): HistoryEntry => ({ id: r.id, taskId: r.task_id, title: r.title, summary: r.summary, ts: r.ts });

/** FTS5 query from free text: every word quoted and ORed, so punctuation in a goal can never break the query. */
export function ftsQuery(text: string): string | null {
  const words = text
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter((w) => w.length >= 3);
  return words.length ? [...new Set(words)].map((w) => `"${w}"`).join(" OR ") : null;
}

export class D1MemoryStore implements MemoryStore {
  private readonly db: D1Database;
  constructor(db: D1Database) {
    this.db = db;
  }

  async getBlock(name: BlockName): Promise<string> {
    const r = await this.db.prepare("SELECT body FROM memory_blocks WHERE name = ?").bind(name).first<{ body: string }>();
    return r?.body ?? "";
  }

  async setBlock(name: BlockName, text: string): Promise<void> {
    await this.db
      .prepare("INSERT INTO memory_blocks (name, body) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET body = excluded.body")
      .bind(name, text)
      .run();
  }

  async addHistory(e: HistoryEntry): Promise<void> {
    await this.db.batch([
      this.db
        .prepare(
          "INSERT INTO memory_history (id, task_id, title, summary, ts) VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET title = excluded.title, summary = excluded.summary, ts = excluded.ts",
        )
        .bind(e.id, e.taskId, e.title, e.summary, e.ts),
      this.db.prepare("DELETE FROM memory_history_fts WHERE id = ?").bind(e.id),
      this.db.prepare("INSERT INTO memory_history_fts (id, title, summary) VALUES (?, ?, ?)").bind(e.id, e.title, e.summary),
    ]);
  }

  async deleteHistory(id: string): Promise<void> {
    await this.db.batch([
      this.db.prepare("DELETE FROM memory_history WHERE id = ?").bind(id),
      this.db.prepare("DELETE FROM memory_history_fts WHERE id = ?").bind(id),
    ]);
  }

  async listHistory(limit: number): Promise<HistoryEntry[]> {
    const { results } = await this.db.prepare("SELECT * FROM memory_history ORDER BY ts DESC LIMIT ?").bind(limit).all<HistoryRow>();
    return results.map(toEntry);
  }

  async searchHistory(query: string, limit: number): Promise<HistoryEntry[]> {
    const q = ftsQuery(query);
    if (!q) return [];
    const { results } = await this.db
      .prepare(
        "SELECT h.* FROM memory_history_fts f JOIN memory_history h ON h.id = f.id WHERE memory_history_fts MATCH ? ORDER BY bm25(memory_history_fts) LIMIT ?",
      )
      .bind(q, limit)
      .all<HistoryRow>();
    return results.map(toEntry);
  }
}
