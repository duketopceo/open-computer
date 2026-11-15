export const BLOCK_NAMES = ["agent_notes", "user_profile"] as const;
export type BlockName = (typeof BLOCK_NAMES)[number];

export interface HistoryEntry {
  /** Stable id, `task:<task id>` for a finished task. */
  id: string;
  taskId: string;
  title: string;
  summary: string;
  ts: string;
}

/** Storage port for both memory tiers. D1 implements it in the cloud; the in-memory version serves tests and the local core. */
export interface MemoryStore {
  getBlock(name: BlockName): Promise<string>;
  setBlock(name: BlockName, text: string): Promise<void>;
  addHistory(entry: HistoryEntry): Promise<void>;
  /** Every history entry, newest first. Ranking happens in `recall.ts`, so stores stay simple. */
  listHistory(limit: number): Promise<HistoryEntry[]>;
}

export class MemoryMemoryStore implements MemoryStore {
  private readonly blocks = new Map<BlockName, string>();
  readonly history: HistoryEntry[] = [];
  async getBlock(name: BlockName): Promise<string> {
    return this.blocks.get(name) ?? "";
  }
  async setBlock(name: BlockName, text: string): Promise<void> {
    this.blocks.set(name, text);
  }
  async addHistory(entry: HistoryEntry): Promise<void> {
    const i = this.history.findIndex((h) => h.id === entry.id);
    if (i >= 0) this.history[i] = entry;
    else this.history.unshift(entry);
  }
  async listHistory(limit: number): Promise<HistoryEntry[]> {
    return this.history.slice(0, limit);
  }
}
