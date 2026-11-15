import type { HistoryEntry, MemoryStore } from "./store.ts";

export interface RecalledItem {
  id: string;
  title: string;
  /** Where the item came from, cited with it: `task:<id>` for history. */
  source: string;
  text: string;
  score: number;
}

const STOPWORDS = new Set(["the", "and", "for", "with", "that", "this", "from", "have", "are", "was", "you", "your", "not", "but"]);

export function terms(text: string): string[] {
  return text
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter((w) => w.length >= 3 && !STOPWORDS.has(w));
}

function score(query: string[], entry: HistoryEntry): number {
  const doc = terms(`${entry.title} ${entry.summary}`);
  if (doc.length === 0) return 0;
  const counts = new Map<string, number>();
  for (const t of doc) counts.set(t, (counts.get(t) ?? 0) + 1);
  let hits = 0;
  for (const q of new Set(query)) hits += counts.get(q) ?? 0;
  return hits / Math.sqrt(doc.length);
}

/** Keyword recall over task history. Hybrid keyword plus vector search replaces this in a later PR (U20). */
export async function recallHistory(store: MemoryStore, query: string, limit: number): Promise<RecalledItem[]> {
  const q = terms(query);
  if (q.length === 0 || limit <= 0) return [];
  if (store.searchHistory) {
    const ranked = await store.searchHistory(query, limit);
    return ranked.map((e, i) => ({ id: e.id, title: e.title, source: `task:${e.taskId}`, text: e.summary, score: ranked.length - i }));
  }
  const entries = await store.listHistory(500);
  return entries
    .map((e, order) => ({ e, s: score(q, e), order }))
    .filter((x) => x.s > 0)
    .sort((a, b) => b.s - a.s || a.order - b.order)
    .slice(0, limit)
    .map(({ e, s }) => ({ id: e.id, title: e.title, source: `task:${e.taskId}`, text: e.summary, score: s }));
}
