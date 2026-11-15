import type { APPROVAL_KINDS, APPROVAL_STATES } from "../events.ts";

export type ApprovalKind = (typeof APPROVAL_KINDS)[number];
export type ApprovalState = (typeof APPROVAL_STATES)[number];
export type ApprovalOutcome = Exclude<ApprovalState, "requested">;

export const DEFAULT_APPROVAL_TTL_MS = 24 * 60 * 60 * 1000;

export interface ApprovalRecord {
  id: string;
  taskId: string;
  kind: ApprovalKind;
  hash: string;
  summary: string;
  /** Dollars a cap-raise approval adds to the task cap. */
  amount?: number;
  state: ApprovalState;
  createdAt: number;
  expiresAt: number;
}

export interface ApprovalRequestInput {
  taskId: string;
  kind: ApprovalKind;
  hash: string;
  summary: string;
  amount?: number;
}

/** The only approval surface the agent loop receives. It can ask and wait; it cannot decide. */
export interface ApprovalRequester {
  request(input: ApprovalRequestInput): Promise<ApprovalRecord>;
  wait(approvalId: string): Promise<ApprovalOutcome>;
  get(approvalId: string): ApprovalRecord | undefined;
}

export type DecideResult =
  | { ok: true; record: ApprovalRecord }
  | { ok: false; reason: "unknown" | "hash_mismatch" | "already_decided" | "expired" };

export type Verdict =
  | { ok: true; next: "approved" | "denied" }
  | { ok: false; reason: "hash_mismatch" | "already_decided" | "expired" };

/**
 * The decision rules in one pure function, shared by the in-memory queue and the durable one in the cloud
 * core: expiry first, then single use, then the hash the human was shown.
 */
export function evaluateDecision(
  record: Pick<ApprovalRecord, "state" | "hash" | "expiresAt">,
  decision: { hash: string; approved: boolean; actor: "human" },
  now: number,
): Verdict {
  if (record.state === "expired") return { ok: false, reason: "expired" };
  if (record.state !== "requested") return { ok: false, reason: "already_decided" };
  if (now >= record.expiresAt) return { ok: false, reason: "expired" };
  if (decision.hash !== record.hash) return { ok: false, reason: "hash_mismatch" };
  return { ok: true, next: decision.approved ? "approved" : "denied" };
}

export interface ApprovalQueueOptions {
  ttlMs?: number;
  now?: () => number;
  newId?: () => string;
}

/**
 * Human-bound approvals. A decision must present the hash the request carried (so it covers exactly the
 * reviewed action), works once, and fails after expiry. The loop gets `requester`, which has no decide.
 */
export class ApprovalQueue {
  readonly requester: ApprovalRequester;
  private readonly records = new Map<string, ApprovalRecord>();
  private readonly waiters = new Map<string, Array<(o: ApprovalOutcome) => void>>();
  private readonly timers = new Map<string, ReturnType<typeof setTimeout>>();
  private readonly ttlMs: number;
  private readonly now: () => number;
  private readonly newId: () => string;

  constructor(options: ApprovalQueueOptions = {}) {
    this.ttlMs = options.ttlMs ?? DEFAULT_APPROVAL_TTL_MS;
    this.now = options.now ?? (() => Date.now());
    this.newId = options.newId ?? (() => crypto.randomUUID());
    this.requester = {
      request: (input) => this.request(input),
      wait: (id) => this.wait(id),
      get: (id) => this.records.get(id),
    };
  }

  private async request(input: ApprovalRequestInput): Promise<ApprovalRecord> {
    const createdAt = this.now();
    const record: ApprovalRecord = {
      id: this.newId(),
      ...input,
      state: "requested",
      createdAt,
      expiresAt: createdAt + this.ttlMs,
    };
    this.records.set(record.id, record);
    if (Number.isFinite(this.ttlMs) && this.ttlMs < 2 ** 31) {
      this.timers.set(
        record.id,
        setTimeout(() => this.expire(record.id), this.ttlMs),
      );
    }
    return { ...record };
  }

  private settle(id: string, state: Exclude<ApprovalState, "requested">): void {
    const record = this.records.get(id);
    if (!record) return;
    record.state = state;
    const timer = this.timers.get(id);
    if (timer) clearTimeout(timer);
    this.timers.delete(id);
    for (const resolve of this.waiters.get(id) ?? []) resolve(state);
    this.waiters.delete(id);
  }

  private expire(id: string): void {
    const record = this.records.get(id);
    if (record && record.state === "requested") this.settle(id, "expired");
  }

  private wait(id: string): Promise<ApprovalOutcome> {
    const record = this.records.get(id);
    if (!record) return Promise.reject(new Error("unknown approval"));
    if (record.state !== "requested") return Promise.resolve(record.state);
    return new Promise((resolve) => {
      const list = this.waiters.get(id) ?? [];
      list.push(resolve);
      this.waiters.set(id, list);
    });
  }

  /** Human-facing decision path. */
  decide(id: string, decision: { hash: string; approved: boolean; actor: "human" }): DecideResult {
    const record = this.records.get(id);
    if (!record) return { ok: false, reason: "unknown" };
    const verdict = evaluateDecision(record, decision, this.now());
    if (verdict.ok) this.settle(id, verdict.next);
    else if (verdict.reason === "expired") this.settle(id, "expired");
    return verdict.ok ? { ok: true, record: { ...record } } : verdict;
  }

  pending(): ApprovalRecord[] {
    return [...this.records.values()].filter((r) => r.state === "requested").map((r) => ({ ...r }));
  }

  /** Stops timers so a finished process can exit. */
  dispose(): void {
    for (const t of this.timers.values()) clearTimeout(t);
    this.timers.clear();
  }
}
