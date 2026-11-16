import type { Redactor } from "../audit/redact.ts";
import type { BlockName, MemoryStore } from "./store.ts";

export const DEFAULT_BLOCK_CAPS: Record<BlockName, number> = { agent_notes: 2200, user_profile: 1400 };

export type BlockResult = { ok: true; text: string } | { ok: false; error: string };

/** Tier 1: small always-loaded blocks the agent edits. Writes over the character cap are refused with the room left. */
export class BlockManager {
  private readonly store: MemoryStore;
  private readonly caps: Record<BlockName, number>;
  private readonly redactor: Redactor;

  constructor(store: MemoryStore, redactor: Redactor, caps: Partial<Record<BlockName, number>> = {}) {
    this.store = store;
    this.redactor = redactor;
    this.caps = { ...DEFAULT_BLOCK_CAPS, ...caps };
  }

  private async commit(name: BlockName, next: string): Promise<BlockResult> {
    const cap = this.caps[name];
    if (next.length > cap) {
      const current = await this.store.getBlock(name);
      return {
        ok: false,
        error: `That would make ${name} ${next.length} characters; the limit is ${cap} and ${Math.max(0, cap - current.length)} remain. Shorten the text or replace older content.`,
      };
    }
    await this.store.setBlock(name, next);
    return { ok: true, text: next };
  }

  get(name: BlockName): Promise<string> {
    return this.store.getBlock(name);
  }

  async append(name: BlockName, text: string): Promise<BlockResult> {
    const current = await this.store.getBlock(name);
    const addition = this.redactor.redact(text.trim());
    return this.commit(name, current ? `${current}\n${addition}` : addition);
  }

  async replace(name: BlockName, find: string, replacement: string): Promise<BlockResult> {
    const current = await this.store.getBlock(name);
    if (!find || !current.includes(find)) return { ok: false, error: `The text to replace was not found in ${name}.` };
    return this.commit(name, current.replace(find, this.redactor.redact(replacement)));
  }

  async remove(name: BlockName, find: string): Promise<BlockResult> {
    const current = await this.store.getBlock(name);
    if (!find || !current.includes(find)) return { ok: false, error: `The text to remove was not found in ${name}.` };
    return this.commit(name, current.replace(find, "").replace(/\n{2,}/g, "\n").trim());
  }
}
