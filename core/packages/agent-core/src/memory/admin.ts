import { BLOCK_NAMES, type MemoryStore } from "./store.ts";

export interface MemoryStats {
  blocks: Array<{ name: string; chars: number }>;
  entries: number;
  /** When the newest history entry was written, or null with none. */
  newest: string | null;
}

/** What memory holds, as sizes and counts only: no text ever leaves through this. */
export async function memoryStats(store: Pick<MemoryStore, "getBlock" | "listHistory">): Promise<MemoryStats> {
  const blocks = [];
  for (const name of BLOCK_NAMES) blocks.push({ name, chars: (await store.getBlock(name)).length });
  const history = await store.listHistory(100000);
  return { blocks, entries: history.length, newest: history[0]?.ts ?? null };
}

/** Empties both memory tiers: the small blocks and every history entry. Returns how many entries were removed. */
export async function clearMemory(store: MemoryStore & { deleteHistory(id: string): Promise<void> }): Promise<{ cleared: number }> {
  for (const name of BLOCK_NAMES) await store.setBlock(name, "");
  const history = await store.listHistory(100000);
  for (const h of history) await store.deleteHistory(h.id);
  return { cleared: history.length };
}
