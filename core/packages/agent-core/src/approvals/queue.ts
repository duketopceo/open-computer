export type ApprovalKind = "tool" | "cap_raise";
export type ApprovalState = "requested" | "approved" | "denied" | "expired";
export type ApprovalOutcome = "approved" | "denied" | "expired";

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
    this.ttlMs = options.ttlMs ?? 24 * 60 * 60 * 1000;
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
    if (record.state === "expired") return { ok: false, reason: "expired" };
    if (record.state !== "requested") return { ok: false, reason: "already_decided" };
    if (this.now() >= record.expiresAt) {
      this.settle(id, "expired");
      return { ok: false, reason: "expired" };
    }
    if (decision.hash !== record.hash) return { ok: false, reason: "hash_mismatch" };
    this.settle(id, decision.approved ? "approved" : "denied");
    return { ok: true, record: { ...record } };
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
