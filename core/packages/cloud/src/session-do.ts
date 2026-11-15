import { DurableObject } from "cloudflare:workers";
import {
  CostGuard,
  Memory,
  ToolRegistry,
  auditEntryForEvent,
  createRedactor,
  createState,
  defaultPolicy,
  evaluateDecision,
  resolveApproval,
  staticPriceBook,
  step,
  type AgentDeps,
  type AgentEvent,
  type AgentState,
  type ApprovalRecord,
  type ApprovalRequester,
  type EventBody,
  type Provider,
  type StepKind,
  DEFAULT_APPROVAL_TTL_MS,
  isTerminal,
  type ApprovalOutcome,
} from "@open-computer/agent-core";
import type { Env } from "./env.ts";
import { buildProvider } from "./models/gateway.ts";
import { SandboxManager, sandboxTools, type SandboxFactory } from "./sandbox/port.ts";
import { cloudflareSandbox } from "./sandbox/cloudflare-port.ts";
import { D1MemoryStore } from "./storage/d1-memory.ts";
import { ledgerPort, ledgerStub } from "./storage/ledger-client.ts";

const BASE_SYSTEM =
  "You are Open Computer, an agent that finishes tasks for the user. Use the tools you are given, one step at a time, and say plainly when you are done.";

export interface StartTaskInput {
  taskId: string;
  sessionId: string;
  goal: string;
  model: string;
  taskCap: number;
  dayCap: number | null;
  memoryEnabled: boolean;
}

export interface RunStepResult {
  kind: StepKind;
  approvalId?: string;
}

export type DecisionResult =
  | { ok: true; outcome: "approved" | "denied" }
  | { ok: false; reason: "unknown" | "hash_mismatch" | "already_decided" | "expired" | "workflow_unavailable" };

interface StoredTask {
  state: AgentState;
  meta: { goal: string; taskCap: number; dayCap: number | null; memoryEnabled: boolean; sessionId: string };
}

interface ApprovalRow extends Record<string, SqlStorageValue> {
  id: string;
  task_id: string;
  kind: string;
  hash: string;
  summary: string;
  amount: number | null;
  state: string;
  created_at: number;
  expires_at: number;
}

const toRecord = (r: ApprovalRow): ApprovalRecord => ({
  id: r.id,
  taskId: r.task_id,
  kind: r.kind as ApprovalRecord["kind"],
  hash: r.hash,
  summary: r.summary,
  ...(r.amount !== null ? { amount: r.amount } : {}),
  state: r.state as ApprovalRecord["state"],
  createdAt: r.created_at,
  expiresAt: r.expires_at,
});

/**
 * One Durable Object per session. It holds the session's state, its event log, the approval inbox and the
 * SSE subscribers. A Workflow drives it one step at a time (KTD3); every step is idempotent by step key, and a
 * retried step first supersedes the events its failed attempt streamed so subscribers never see a delta twice.
 *
 * Subclasses (the test entry) override the three `make*` hooks; production code has no flag that picks a fake.
 */
export class SessionDO extends DurableObject<Env> {
  private nextSeq = 0;
  private readonly subscribers = new Set<ReadableStreamDefaultController<Uint8Array>>();
  private pendingAudit: Array<Promise<unknown>> = [];
  private sandboxes: SandboxManager | undefined;
  /** In-flight steps by task, so a cancel can stop the model stream and the queued tool calls. */
  private readonly aborts = new Map<string, AbortController>();
  private readonly cancelled = new Set<string>();
  private readonly redactor = createRedactor();
  private readonly encoder = new TextEncoder();

  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    const sql = ctx.storage.sql;
    sql.exec("CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY, task_id TEXT NOT NULL, json TEXT NOT NULL)");
    sql.exec("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)");
    sql.exec(
      "CREATE TABLE IF NOT EXISTS approvals (id TEXT PRIMARY KEY, task_id TEXT NOT NULL, kind TEXT NOT NULL, hash TEXT NOT NULL, summary TEXT NOT NULL, amount REAL, state TEXT NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL)",
    );
    sql.exec("CREATE TABLE IF NOT EXISTS steps (step_key TEXT PRIMARY KEY, attempts INTEGER NOT NULL, start_seq INTEGER NOT NULL, result TEXT)");
    const max = sql.exec<{ m: number | null }>("SELECT MAX(seq) AS m FROM events").one().m;
    this.nextSeq = max === null ? 0 : max + 1;
  }

  // ---- hooks ----

  protected makeProvider(): Provider {
    return buildProvider(this.env, (url, init) => fetch(url, init));
  }
  protected makeSandboxFactory(): SandboxFactory {
    return (taskId) => cloudflareSandbox(this.env, taskId);
  }
  protected clock(): number {
    return Date.now();
  }
  private abortFor(taskId: string): AbortController {
    let c = this.aborts.get(taskId);
    if (!c) {
      c = new AbortController();
      this.aborts.set(taskId, c);
    }
    return c;
  }
  private sandboxManager(): SandboxManager {
    this.sandboxes ??= new SandboxManager(this.makeSandboxFactory());
    return this.sandboxes;
  }
  private nowIso(): string {
    return new Date(this.clock()).toISOString();
  }
  private memoryFor(enabled: boolean): Memory {
    return new Memory({ store: new D1MemoryStore(this.env.DB), enabled, redactor: this.redactor });
  }

  // ---- storage helpers ----

  private kvGet<T>(key: string): T | undefined {
    const row = this.ctx.storage.sql.exec<{ value: string }>("SELECT value FROM kv WHERE key = ?", key).toArray()[0];
    return row ? (JSON.parse(row.value) as T) : undefined;
  }
  private kvSet(key: string, value: unknown): void {
    this.ctx.storage.sql.exec("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", key, JSON.stringify(value));
  }
  private loadTask(taskId: string): StoredTask {
    const t = this.kvGet<StoredTask>(`task:${taskId}`);
    if (!t) throw new Error(`Unknown task ${taskId}.`);
    return t;
  }

  // ---- events ----

  private broadcast(chunk: string): void {
    const bytes = this.encoder.encode(chunk);
    for (const c of this.subscribers) {
      try {
        c.enqueue(bytes);
      } catch {
        this.subscribers.delete(c);
      }
    }
  }

  private frame(seq: number, json: string): string {
    return `id: ${seq}\ndata: ${json}\n\n`;
  }

  private appendEvent(taskId: string, body: EventBody): AgentEvent {
    const event = { ...body, task_id: taskId, seq: this.nextSeq, ts: this.nowIso() } as AgentEvent;
    this.nextSeq += 1;
    const json = JSON.stringify(event);
    this.ctx.storage.sql.exec("INSERT INTO events (seq, task_id, json) VALUES (?, ?, ?)", event.seq, taskId, json);
    this.broadcast(this.frame(event.seq, json));
    const entry = auditEntryForEvent(event, false);
    if (entry) this.pendingAudit.push(ledgerStub(this.env).appendAudit(entry.kind, entry.taskId, this.redactor.redact(entry.data)));
    return event;
  }

  private closeSubscribers(): void {
    for (const c of this.subscribers) {
      try {
        c.close();
      } catch {
        /* already closed */
      }
    }
    this.subscribers.clear();
  }

  private async flushAudit(): Promise<void> {
    const pending = this.pendingAudit;
    this.pendingAudit = [];
    await Promise.all(pending);
  }

  async eventsAfter(afterSeq: number): Promise<AgentEvent[]> {
    return this.ctx.storage.sql
      .exec<{ json: string }>("SELECT json FROM events WHERE seq > ? ORDER BY seq ASC", afterSeq)
      .toArray()
      .map((r) => JSON.parse(r.json) as AgentEvent);
  }

  /** True once the latest task has finished and its closing events (memory write) are stored. */
  private settled(): boolean {
    return this.kvGet<boolean>("settled") === true;
  }

  /** SSE: replays everything after Last-Event-ID, then streams live until the run ends. */
  override async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname !== "/events") return new Response("Not found", { status: 404 });
    const header = request.headers.get("last-event-id");
    const after = Number(header ?? url.searchParams.get("after") ?? "-1");
    const backlog = await this.eventsAfter(Number.isFinite(after) ? after : -1);
    const closeAfterBacklog = this.settled();
    let self: ReadableStreamDefaultController<Uint8Array>;
    const stream = new ReadableStream<Uint8Array>({
      start: (controller) => {
        self = controller;
        controller.enqueue(this.encoder.encode(": open\n\n"));
        for (const e of backlog) controller.enqueue(this.encoder.encode(this.frame(e.seq, JSON.stringify(e))));
        if (closeAfterBacklog) controller.close();
        else this.subscribers.add(controller);
      },
      cancel: () => {
        this.subscribers.delete(self);
      },
    });
    return new Response(stream, { headers: { "content-type": "text/event-stream", "cache-control": "no-store" } });
  }

  // ---- approvals ----

  private approvalRequester(): ApprovalRequester {
    return {
      request: async (input) => {
        const now = this.clock();
        const id = crypto.randomUUID();
        this.ctx.storage.sql.exec(
          "INSERT INTO approvals (id, task_id, kind, hash, summary, amount, state, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, 'requested', ?, ?)",
          id,
          input.taskId,
          input.kind,
          input.hash,
          input.summary,
          input.amount ?? null,
          now,
          now + DEFAULT_APPROVAL_TTL_MS,
        );
        return this.getApproval(id) as ApprovalRecord;
      },
      wait: () => Promise.reject(new Error("The cloud driver waits on Workflow events, not on the queue.")),
      get: (id) => this.getApproval(id),
    };
  }

  private getApproval(id: string): ApprovalRecord | undefined {
    const row = this.ctx.storage.sql.exec<ApprovalRow>("SELECT * FROM approvals WHERE id = ?", id).toArray()[0];
    return row ? toRecord(row) : undefined;
  }

  pendingApprovals(): ApprovalRecord[] {
    return this.ctx.storage.sql
      .exec<ApprovalRow>("SELECT * FROM approvals WHERE state = 'requested' ORDER BY created_at")
      .toArray()
      .map(toRecord);
  }

  /** Human-facing decision. Hash-bound, single-use and time-limited; resumes the waiting Workflow on success. */
  async decideApproval(approvalId: string, decision: { hash: string; approved: boolean }): Promise<DecisionResult> {
    const record = this.getApproval(approvalId);
    if (!record) return { ok: false, reason: "unknown" };
    const verdict = evaluateDecision(record, { ...decision, actor: "human" }, this.clock());
    if (!verdict.ok) {
      if (verdict.reason === "expired" && record.state === "requested") {
        this.ctx.storage.sql.exec("UPDATE approvals SET state = 'expired' WHERE id = ?", approvalId);
      }
      return verdict;
    }
    this.ctx.storage.sql.exec("UPDATE approvals SET state = ? WHERE id = ?", verdict.next, approvalId);
    this.pendingAudit.push(
      ledgerStub(this.env).appendAudit("approval.decided", record.taskId, { approval_id: approvalId, outcome: verdict.next, kind: record.kind }),
    );
    await this.flushAudit();
    try {
      await (await this.env.TASK_WORKFLOW.get(record.taskId)).sendEvent({ type: "approval", payload: { approvalId, outcome: verdict.next } });
    } catch {
      // The Workflow was not told, so the decision must stay open: a repeat with the same hash can still land.
      this.ctx.storage.sql.exec("UPDATE approvals SET state = 'requested' WHERE id = ?", approvalId);
      return { ok: false, reason: "workflow_unavailable" };
    }
    return { ok: true, outcome: verdict.next };
  }

  // ---- tasks ----

  /** One task at a time per session: a second one would share the event log and close the first one's stream. */
  async startTask(input: StartTaskInput): Promise<{ taskId: string } | { busy: true }> {
    if (this.kvGet<boolean>("settled") === false) return { busy: true };
    const memory = this.memoryFor(input.memoryEnabled);
    const prepared = await memory.prepare(input.goal);
    const system = prepared.section ? `${BASE_SYSTEM}\n\n${prepared.section}` : BASE_SYSTEM;
    const state = createState({
      taskId: input.taskId,
      model: input.model,
      system,
      goal: input.goal,
      sandboxId: input.taskId,
      startedAtMs: this.clock(),
    });
    const meta = { goal: input.goal, taskCap: input.taskCap, dayCap: input.dayCap, memoryEnabled: input.memoryEnabled, sessionId: input.sessionId };
    this.kvSet(`task:${input.taskId}`, { state, meta } satisfies StoredTask);
    this.kvSet("settled", false);
    await this.env.DB.prepare("INSERT INTO tasks (task_id, session_id, goal, status, created_at) VALUES (?, ?, ?, 'running', ?)")
      .bind(input.taskId, input.sessionId, input.goal, this.nowIso())
      .run();
    this.appendEvent(input.taskId, { type: "run", status: "running" });
    if (prepared.recalled.length > 0) {
      this.appendEvent(input.taskId, {
        type: "memory_recall",
        items: prepared.recalled.map((r) => ({ id: r.id, title: r.title, source: r.source })),
      });
    }
    await this.flushAudit();
    return { taskId: input.taskId };
  }

  private depsFor(taskId: string, stored: StoredTask): AgentDeps {
    const memory = this.memoryFor(stored.meta.memoryEnabled);
    const sandboxes = this.sandboxManager();
    return {
      provider: this.makeProvider(),
      tools: new ToolRegistry([...memory.tools(), ...sandboxTools(sandboxes, taskId)]),
      policy: defaultPolicy,
      approvals: this.approvalRequester(),
      guard: new CostGuard(ledgerPort(this.env), { taskCap: stored.meta.taskCap, dayCap: stored.meta.dayCap, now: () => this.clock() }),
      prices: staticPriceBook({}),
      emit: (body) => {
        // Once cancelled, an in-flight step must not add events after the cancel.
        if (!this.cancelled.has(taskId)) this.appendEvent(taskId, body);
      },
      now: () => this.clock(),
      signal: this.abortFor(taskId).signal,
    };
  }

  /** Runs one agent step. Idempotent per step key; a retry supersedes the failed attempt's streamed events. */
  async runStep(taskId: string, stepKey: string): Promise<RunStepResult> {
    const sql = this.ctx.storage.sql;
    const prior = sql.exec<{ attempts: number; start_seq: number; result: string | null }>("SELECT * FROM steps WHERE step_key = ?", stepKey).toArray()[0];
    if (prior?.result) return JSON.parse(prior.result) as RunStepResult;

    let startSeq = this.nextSeq;
    if (prior) {
      startSeq = prior.start_seq;
      sql.exec("DELETE FROM events WHERE seq >= ?", startSeq);
      this.broadcast(`event: supersede\ndata: ${JSON.stringify({ from_seq: startSeq })}\n\n`);
      this.nextSeq = startSeq;
    }
    sql.exec(
      "INSERT INTO steps (step_key, attempts, start_seq, result) VALUES (?, ?, ?, NULL) ON CONFLICT(step_key) DO UPDATE SET attempts = attempts + 1, start_seq = excluded.start_seq",
      stepKey,
      (prior?.attempts ?? 0) + 1,
      startSeq,
    );

    const stored = this.loadTask(taskId);
    const outcome = await step(stored.state, this.depsFor(taskId, stored));
    await this.flushAudit();
    // A cancel can land while the step awaits the model or a tool; the cancel is final, so this outcome is dropped.
    if (isTerminal(this.loadTask(taskId).state.status)) {
      const cancelled: RunStepResult = { kind: "cancelled" };
      sql.exec("UPDATE steps SET result = ? WHERE step_key = ?", JSON.stringify(cancelled), stepKey);
      return cancelled;
    }
    this.kvSet(`task:${taskId}`, { ...stored, state: outcome.state } satisfies StoredTask);

    if (isTerminal(outcome.kind)) await this.finalize(taskId, stored, outcome.state);
    const result: RunStepResult = {
      kind: outcome.kind,
      ...(outcome.state.pending ? { approvalId: outcome.state.pending.approvalId } : {}),
    };
    sql.exec("UPDATE steps SET result = ? WHERE step_key = ?", JSON.stringify(result), stepKey);
    return result;
  }

  /** Records the human decision on the task state so the next step acts on it. Idempotent. */
  async resolvePendingApproval(taskId: string, approvalId: string, outcome: ApprovalOutcome): Promise<void> {
    const stored = this.loadTask(taskId);
    if (stored.state.pending?.approvalId !== approvalId || stored.state.pending.outcome) return;
    if (outcome === "expired") this.ctx.storage.sql.exec("UPDATE approvals SET state = 'expired' WHERE id = ? AND state = 'requested'", approvalId);
    const rec = this.getApproval(approvalId);
    this.appendEvent(taskId, {
      type: "approval",
      approval_id: approvalId,
      kind: stored.state.pending.kind,
      state: outcome,
      hash: stored.state.pending.hash,
      summary: rec?.summary ?? "",
    });
    this.appendEvent(taskId, { type: "run", status: "running" });
    await this.flushAudit();
    this.kvSet(`task:${taskId}`, { ...stored, state: resolveApproval(stored.state, outcome) } satisfies StoredTask);
  }

  async cancelTask(taskId: string): Promise<void> {
    const stored = this.loadTask(taskId);
    if (isTerminal(stored.state.status)) return;
    this.cancelled.add(taskId);
    this.aborts.get(taskId)?.abort();
    this.appendEvent(taskId, { type: "run", status: "cancelled", reason: "Cancelled by the user." });
    const state: AgentState = { ...stored.state, status: "cancelled", pending: undefined, remainingCalls: [] };
    this.kvSet(`task:${taskId}`, { ...stored, state } satisfies StoredTask);
    await this.flushAudit();
    await this.finalize(taskId, stored, state);
    try {
      await (await this.env.TASK_WORKFLOW.get(taskId)).terminate();
    } catch {
      /* the instance already ended */
    }
  }

  /** Ends a task that can no longer make progress (retries exhausted, Workflow could not start). */
  async failTask(taskId: string, reason: string): Promise<void> {
    const stored = this.loadTask(taskId);
    if (isTerminal(stored.state.status)) return;
    this.appendEvent(taskId, { type: "run", status: "failed", reason });
    const state: AgentState = { ...stored.state, status: "failed", failureReason: reason, pending: undefined, remainingCalls: [] };
    this.kvSet(`task:${taskId}`, { ...stored, state } satisfies StoredTask);
    await this.flushAudit().catch(() => undefined);
    await this.finalize(taskId, stored, state);
  }

  private async finalize(taskId: string, stored: StoredTask, state: AgentState): Promise<void> {
    await this.env.DB.prepare("UPDATE tasks SET status = ? WHERE task_id = ?").bind(state.status, taskId).run();
    if (state.status === "done") {
      const memory = this.memoryFor(stored.meta.memoryEnabled);
      const last = [...state.messages].reverse().find((m) => m.role === "assistant");
      const id = await memory.recordTask({
        taskId,
        goal: stored.meta.goal,
        outcome: typeof last?.content === "string" ? last.content.slice(0, 500) : "Finished.",
        ts: this.nowIso(),
      });
      if (id) this.appendEvent(taskId, { type: "memory_write", id, tier: "history" });
      await this.flushAudit();
    }
    await this.sandboxManager().release(taskId);
    this.kvSet("settled", true);
    this.closeSubscribers();
  }

  taskState(taskId: string): AgentState | undefined {
    return this.kvGet<StoredTask>(`task:${taskId}`)?.state;
  }
}
