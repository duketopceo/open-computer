import { sha256Hex, stableStringify } from "../approvals/hash.ts";
import type { AgentEvent } from "../events.ts";
import { createRedactor, type Redactor } from "./redact.ts";

export interface AuditRecord {
  seq: number;
  ts: string;
  task_id: string | null;
  kind: string;
  data: Record<string, unknown>;
  prev_hash: string;
  hash: string;
}

export const GENESIS_HASH = "0".repeat(64);

/**
 * Storage port. `appendNext` must run `make` and persist its result as one serialized step, so two
 * concurrent appenders can never read the same last record. The Cloudflare driver implements it inside a
 * single Durable Object; the in-memory version below serializes with a promise chain.
 */
export interface AuditStorage {
  appendNext(make: (last: AuditRecord | null) => Promise<AuditRecord>): Promise<AuditRecord>;
  read(fromSeq: number, limit: number): Promise<AuditRecord[]>;
}

export class MemoryAuditStorage implements AuditStorage {
  readonly records: AuditRecord[] = [];
  private chain: Promise<unknown> = Promise.resolve();

  appendNext(make: (last: AuditRecord | null) => Promise<AuditRecord>): Promise<AuditRecord> {
    const run = this.chain.then(async () => {
      const record = await make(this.records.at(-1) ?? null);
      this.records.push(record);
      return record;
    });
    this.chain = run.catch(() => undefined);
    return run;
  }
  async read(fromSeq: number, limit: number): Promise<AuditRecord[]> {
    return this.records.filter((r) => r.seq >= fromSeq).slice(0, limit);
  }
}

async function hashRecord(prev: string, body: Omit<AuditRecord, "prev_hash" | "hash">): Promise<string> {
  return sha256Hex(prev + stableStringify(body));
}

/** Builds the next chained record from the previous one. Storage implementations call this inside their serialized append. */
export async function nextAuditRecord(
  last: AuditRecord | null,
  input: { ts: string; taskId: string | null; kind: string; data: Record<string, unknown> },
): Promise<AuditRecord> {
  const seq = last ? last.seq + 1 : 0;
  const prev = last ? last.hash : GENESIS_HASH;
  const body = { seq, ts: input.ts, task_id: input.taskId, kind: input.kind, data: input.data };
  return { ...body, prev_hash: prev, hash: await hashRecord(prev, body) };
}

export interface AuditLogOptions {
  storage: AuditStorage;
  redactor?: Redactor;
  now?: () => number;
  /** Prompt and completion text stay out of the log unless this is on. */
  captureContent?: boolean;
}

export class AuditLog {
  readonly storage: AuditStorage;
  private readonly redactor: Redactor;
  private readonly now: () => number;
  private readonly captureContent: boolean;

  constructor(options: AuditLogOptions) {
    this.storage = options.storage;
    this.redactor = options.redactor ?? createRedactor();
    this.now = options.now ?? (() => Date.now());
    this.captureContent = options.captureContent ?? false;
  }

  append(kind: string, taskId: string | null, data: Record<string, unknown>): Promise<AuditRecord> {
    const cleaned = this.redactor.redact(data);
    const ts = new Date(this.now()).toISOString();
    return this.storage.appendNext((last) => nextAuditRecord(last, { ts, taskId, kind, data: cleaned }));
  }

  /** Records what an event says about an action, approval or model call; delta streams and bulk text are not kept. */
  async recordEvent(event: AgentEvent): Promise<AuditRecord | null> {
    const entry = auditEntryForEvent(event, this.captureContent);
    return entry ? this.append(entry.kind, entry.taskId, entry.data) : null;
  }
}

export interface AuditEntry {
  kind: string;
  taskId: string;
  data: Record<string, unknown>;
}

/** What the audit log keeps for an event, or null for events it does not keep. Shared by every storage backend. */
export function auditEntryForEvent(event: AgentEvent, captureContent = false): AuditEntry | null {
  const { task_id, seq: _seq, ts: _ts, ...rest } = event;
  switch (event.type) {
    case "message.delta":
    case "reasoning.delta":
    case "tool_call.args.delta":
    case "tool_call.started":
      return null;
    case "message.done": {
      const { text, ...meta } = rest as { text: string };
      return { kind: "event:message.done", taskId: task_id, data: captureContent ? { ...meta, text } : { ...meta, chars: text.length } };
    }
    case "tool_call.result": {
      const { output, ...meta } = rest as { output: string };
      return { kind: "event:tool_call.result", taskId: task_id, data: captureContent ? { ...meta, output } : { ...meta, chars: output.length } };
    }
    default:
      return { kind: `event:${event.type}`, taskId: task_id, data: rest as Record<string, unknown> };
  }
}

export async function verifyChain(records: AuditRecord[]): Promise<{ ok: true } | { ok: false; at: number; reason: string }> {
  let prev = GENESIS_HASH;
  let expectedSeq = records[0]?.seq ?? 0;
  for (const r of records) {
    if (r.seq !== expectedSeq) return { ok: false, at: r.seq, reason: "sequence gap" };
    if (r.prev_hash !== prev) return { ok: false, at: r.seq, reason: "previous hash mismatch" };
    const { prev_hash: _p, hash, ...body } = r;
    if ((await hashRecord(prev, body)) !== hash) return { ok: false, at: r.seq, reason: "record hash mismatch" };
    prev = r.hash;
    expectedSeq += 1;
  }
  return { ok: true };
}
