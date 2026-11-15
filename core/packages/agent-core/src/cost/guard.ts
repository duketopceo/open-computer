/**
 * Spend caps that reserve before they spend. The ledger port is the only place totals live, so a
 * Cloudflare deployment can back it with a single serialized writer (U9) while tests use the in-memory one.
 */

export interface LedgerPort {
  /**
   * Atomically checks task and day caps against current totals plus this reservation and, when both fit,
   * records it. Re-reserving the same step id replaces its earlier amount (idempotent for retries).
   */
  tryReserve(input: {
    taskId: string;
    stepId: string;
    day: string;
    amount: number;
    taskCap: number;
    dayCap: number | null;
  }): Promise<ReserveResult>;
  /** Replaces a step's reserved amount with the actual cost (or leaves the reservation when null). */
  settle(input: { stepId: string; actual: number | null }): Promise<void>;
  taskSpent(taskId: string): Promise<number>;
  daySpent(day: string): Promise<number>;
  /** Extra dollars approved on top of the base cap for one task. */
  taskCapExtra(taskId: string): Promise<number>;
  addTaskCapExtra(taskId: string, by: number): Promise<void>;
}

export type ReserveResult =
  | { ok: true }
  | { ok: false; scope: "task" | "day"; spent: number; cap: number };

interface Row {
  taskId: string;
  day: string;
  amount: number;
}

export class MemoryLedger implements LedgerPort {
  private readonly rows = new Map<string, Row>();
  private readonly extra = new Map<string, number>();

  private sum(pred: (r: Row) => boolean, except?: string): number {
    let total = 0;
    for (const [id, row] of this.rows) if (id !== except && pred(row)) total += row.amount;
    return total;
  }

  async tryReserve(i: Parameters<LedgerPort["tryReserve"]>[0]): Promise<ReserveResult> {
    const taskSpent = this.sum((r) => r.taskId === i.taskId, i.stepId);
    if (taskSpent + i.amount > i.taskCap + 1e-12) return { ok: false, scope: "task", spent: taskSpent, cap: i.taskCap };
    if (i.dayCap !== null) {
      const daySpent = this.sum((r) => r.day === i.day, i.stepId);
      if (daySpent + i.amount > i.dayCap + 1e-12) return { ok: false, scope: "day", spent: daySpent, cap: i.dayCap };
    }
    this.rows.set(i.stepId, { taskId: i.taskId, day: i.day, amount: i.amount });
    return { ok: true };
  }
  async settle(i: { stepId: string; actual: number | null }): Promise<void> {
    const row = this.rows.get(i.stepId);
    if (row && i.actual !== null) row.amount = i.actual;
  }
  async taskSpent(taskId: string): Promise<number> {
    return this.sum((r) => r.taskId === taskId);
  }
  async daySpent(day: string): Promise<number> {
    return this.sum((r) => r.day === day);
  }
  async taskCapExtra(taskId: string): Promise<number> {
    return this.extra.get(taskId) ?? 0;
  }
  async addTaskCapExtra(taskId: string, by: number): Promise<void> {
    this.extra.set(taskId, (this.extra.get(taskId) ?? 0) + by);
  }
}

export interface GuardConfig {
  taskCap: number;
  dayCap: number | null;
  now?: () => number;
}

export type GuardDecision =
  | { ok: true }
  | { ok: false; scope: "task" | "day"; spent: number; cap: number; needed: number };

export class CostGuard {
  private readonly ledger: LedgerPort;
  private readonly config: GuardConfig;
  private readonly now: () => number;

  constructor(ledger: LedgerPort, config: GuardConfig) {
    this.ledger = ledger;
    this.config = config;
    this.now = config.now ?? (() => Date.now());
  }

  private day(): string {
    return new Date(this.now()).toISOString().slice(0, 10);
  }

  async taskCap(taskId: string): Promise<number> {
    return this.config.taskCap + (await this.ledger.taskCapExtra(taskId));
  }

  /** Reserve an estimate; on refusal the caller raises a cap-raise approval instead of calling the model. */
  async reserve(taskId: string, stepId: string, estimate: number): Promise<GuardDecision> {
    const r = await this.ledger.tryReserve({
      taskId,
      stepId,
      day: this.day(),
      amount: estimate,
      taskCap: await this.taskCap(taskId),
      dayCap: this.config.dayCap,
    });
    return r.ok ? r : { ...r, needed: estimate };
  }

  async settle(stepId: string, actual: number | null): Promise<void> {
    await this.ledger.settle({ stepId, actual });
  }

  async raiseTaskCap(taskId: string, by: number): Promise<void> {
    await this.ledger.addTaskCapExtra(taskId, by);
  }

  async totals(taskId: string): Promise<{ task: number; day: number; taskCap: number; dayCap: number | null }> {
    return {
      task: await this.ledger.taskSpent(taskId),
      day: await this.ledger.daySpent(this.day()),
      taskCap: await this.taskCap(taskId),
      dayCap: this.config.dayCap,
    };
  }
}
