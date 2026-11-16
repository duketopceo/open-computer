import {
  ApprovalQueue,
  AuditLog,
  CostGuard,
  DEFAULT_PROVIDER_TIMEOUTS,
  BASE_SYSTEM_PROMPT,
  EventSequencer,
  Memory,
  OPENROUTER_BASE_URL,
  ProviderError,
  applyProfile,
  SandboxManager,
  ToolRegistry,
  createState,
  createPolicy,
  exportOtlp,
  modelRefusal,
  openRouterProvider,
  parseItPolicy,
  parsePrices,
  priceBookFromTable,
  resolveDailyCaps,
  runLoop,
  sandboxTools,
  webSearchTool,
  type AgentEvent,
  type FetchLike,
  type PriceBook,
  type PriceTable,
  type Provider,
  type SandboxFactory,
} from "@open-computer/agent-core";
import defaults from "../../../../contracts/defaults.json" with { type: "json" };
import type { LocalStore } from "./store.ts";

const PRICE_TTL_MS = 24 * 60 * 60 * 1000;

export interface RuntimeOptions {
  store: LocalStore;
  fetch: FetchLike;
  /** The model key, read per call so a rotated key is picked up. Throws when none is stored. */
  apiKey: () => string | Promise<string>;
  baseUrl?: string;
  sandbox: SandboxFactory;
  /** IT policy JSON text; empty means none, a broken policy fails closed. */
  policyJson?: string;
  /** Optional OTLP/HTTP traces endpoint; the policy's otlp_endpoint takes precedence. */
  otlpEndpoint?: string;
  now?: () => number;
  /** The owner's private profile overlay (JSON text), layered over the public defaults. */
  profileJson?: string;
  /** The key for the custom OpenAI-compatible endpoint, or null when none is stored. */
  customKey?: () => string | null;
  /** Test hook: replaces the OpenRouter provider. */
  provider?: Provider;
  approvalTtlMs?: number;
}

export interface StartTaskInput {
  goal: string;
  sessionId: string;
  model?: string;
  taskCap?: number;
  dayCap?: number;
  memory?: boolean;
}

export type Subscriber = { send(id: number, event: AgentEvent): void; close(): void };

/** The self-hosted agent runtime: the same loop, approvals, memory, audit and spend as the cloud core, in one process. */
/** How long a running task may go without progress before a new task in its session ends it as failed. */
export const STALE_RUN_MS = 15 * 60 * 1000;

export class Runtime {
  readonly store: LocalStore;
  readonly opts: RuntimeOptions;
  readonly approvals: ApprovalQueue;
  readonly sandboxes: SandboxManager;
  readonly audit: AuditLog;
  private readonly subs = new Map<string, Set<Subscriber>>();
  private readonly aborts = new Map<string, AbortController>();
  private readonly running = new Set<Promise<void>>();

  constructor(opts: RuntimeOptions) {
    this.opts = opts;
    this.store = opts.store;
    this.approvals = new ApprovalQueue({ ...(opts.approvalTtlMs ? { ttlMs: opts.approvalTtlMs } : {}), ...(opts.now ? { now: opts.now } : {}) });
    this.sandboxes = new SandboxManager(opts.sandbox);
    this.audit = new AuditLog({ storage: opts.store.audit, ...(opts.now ? { now: opts.now } : {}) });
    this.store.failOrphans();
  }

  private now(): number {
    return this.opts.now ? this.opts.now() : Date.now();
  }

  caps() {
    const stored = this.store.getSetting("daily_cap_usd");
    return resolveDailyCaps(stored === null ? null : Number(stored), parseItPolicy(this.opts.policyJson));
  }

  async prices(): Promise<PriceBook> {
    let table: PriceTable = {};
    let at = 0;
    try {
      table = JSON.parse(this.store.getSetting("price_book") ?? "{}") as PriceTable;
      at = Number(this.store.getSetting("price_book_at") ?? 0);
    } catch {
      table = {};
    }
    if (Object.keys(table).length > 0 && this.now() - at < PRICE_TTL_MS) return priceBookFromTable(table);
    try {
      const res = await this.opts.fetch(`${OPENROUTER_BASE_URL}/models`, { method: "GET", headers: {}, signal: AbortSignal.timeout(8000) });
      if (res.ok) {
        const fresh = parsePrices(await res.json());
        if (Object.keys(fresh).length > 0) {
          this.store.setSetting("price_book", JSON.stringify(fresh));
          this.store.setSetting("price_book_at", String(this.now()));
          return priceBookFromTable(fresh);
        }
      }
    } catch {
      /* keep the stale copy */
    }
    return priceBookFromTable(table);
  }

  /** The user's model choices and custom endpoint, as saved. Keys are never part of this. */
  userSettings(): { default_model: string; worker_model: string; custom_endpoint_url: string | null; custom_endpoint_model: string | null } {
    const base = applyProfile(this.opts.profileJson);
    return {
      default_model: this.store.getSetting("default_model") ?? base.default_model,
      worker_model: this.store.getSetting("worker_model") ?? base.worker_model,
      custom_endpoint_url: this.store.getSetting("custom_endpoint_url"),
      custom_endpoint_model: this.store.getSetting("custom_endpoint_model"),
    };
  }

  /** The provider for a model. The model id "custom" goes to the saved OpenAI-compatible endpoint with its own key. */
  provider(model?: string): Provider {
    if (this.opts.provider) return this.opts.provider;
    if (model === "custom") {
      const u = this.userSettings();
      if (!u.custom_endpoint_url || !u.custom_endpoint_model) {
        return async function* () {
          yield { t: "error", code: "provider_http_error", message: "Set the custom endpoint address and model in Settings first.", retryable: false };
        };
      }
      const base = openRouterProvider({
        fetch: this.opts.fetch,
        baseUrl: u.custom_endpoint_url,
        apiKey: () => {
          const k = this.opts.customKey?.() ?? null;
          if (!k) throw new ProviderError("key_missing", "No key is stored for the custom endpoint yet.");
          return k;
        },
        timeouts: DEFAULT_PROVIDER_TIMEOUTS,
      });
      const target = u.custom_endpoint_model;
      return (request, signal) => base({ ...request, model: target }, signal);
    }
    return openRouterProvider({ fetch: this.opts.fetch, baseUrl: this.opts.baseUrl ?? OPENROUTER_BASE_URL, apiKey: this.opts.apiKey, timeouts: DEFAULT_PROVIDER_TIMEOUTS });
  }

  subscribe(sessionId: string, afterId: number, sub: Subscriber): () => void {
    for (const { id, event } of this.store.eventsAfter(sessionId, afterId)) sub.send(id, event);
    const settled = this.store.activeTaskInSession(sessionId) === null;
    if (settled) {
      sub.close();
      return () => {};
    }
    const set = this.subs.get(sessionId) ?? new Set();
    set.add(sub);
    this.subs.set(sessionId, set);
    return () => void set.delete(sub);
  }

  private closeSession(sessionId: string): void {
    for (const s of this.subs.get(sessionId) ?? []) s.close();
    this.subs.delete(sessionId);
  }

  async startTask(input: StartTaskInput): Promise<{ taskId: string; sessionId: string; dayCap: number } | { busy: true }> {
    const open = this.store.activeTaskInSession(input.sessionId);
    if (open !== null) {
      // A running task with no progress for 15 minutes cannot be making any; end it so the session is not blocked forever. One
      // waiting for an approval is waiting on a person and stays.
      const last = this.store.lastProgressMs(open);
      if (this.store.taskStatus(open) !== "running" || last === null || this.now() - last < STALE_RUN_MS) return { busy: true };
      this.aborts.get(open)?.abort();
      this.store.setTaskStatus(open, "failed");
      this.store.appendEvent(input.sessionId, { type: "run", status: "failed", reason: `No progress for ${STALE_RUN_MS / 60000} minutes, so the task was ended and a new one can start.`, task_id: open, seq: 0, ts: new Date(this.now()).toISOString() } as AgentEvent);
    }
    const refused = modelRefusal(parseItPolicy(this.opts.policyJson), input.model ?? this.userSettings().default_model);
    if (refused) throw new PolicyRefusal(refused);
    const caps = this.caps();
    const dayCap = Math.min(input.dayCap ?? caps.effective, caps.effective);
    const taskCap = input.taskCap ?? 1;
    const taskId = crypto.randomUUID();
    const memory = new Memory({ store: this.store.memory, enabled: input.memory !== false });
    const prepared = await memory.prepare(input.goal);
    const system = prepared.section ? `${BASE_SYSTEM_PROMPT}\n\n${prepared.section}` : BASE_SYSTEM_PROMPT;
    const state = createState({ taskId, model: input.model ?? this.userSettings().default_model, system, goal: input.goal, sandboxId: taskId, startedAtMs: this.now() });
    this.store.insertTask({ taskId, sessionId: input.sessionId, goal: input.goal });
    const job = this.execute({ taskId, sessionId: input.sessionId, goal: input.goal, taskCap, dayCap, memory, state, recalled: prepared.recalled });
    this.running.add(job);
    void job.finally(() => this.running.delete(job));
    return { taskId, sessionId: input.sessionId, dayCap };
  }

  private async execute(j: {
    taskId: string;
    sessionId: string;
    goal: string;
    taskCap: number;
    dayCap: number;
    memory: Memory;
    state: ReturnType<typeof createState>;
    recalled: Array<{ id: string; title: string; source: string }>;
  }): Promise<void> {
    const controller = new AbortController();
    this.aborts.set(j.taskId, controller);
    const sequencer = new EventSequencer(j.taskId, 0, () => new Date(this.now()).toISOString());
    const collected: AgentEvent[] = [];
    const deliver = async (event: AgentEvent): Promise<void> => {
      collected.push(event);
      const id = this.store.appendEvent(j.sessionId, event);
      for (const s of this.subs.get(j.sessionId) ?? []) s.send(id, event);
      await this.audit.recordEvent(event);
    };
    let status = "failed";
    try {
      const it = parseItPolicy(this.opts.policyJson);
      const guard = new CostGuard(this.store.ledger, { taskCap: j.taskCap, dayCap: j.dayCap, now: () => this.now() });
      const tools = new ToolRegistry([
        ...j.memory.tools(),
        ...sandboxTools(this.sandboxes, j.taskId),
        webSearchTool({ fetch: this.opts.fetch, ...(this.opts.baseUrl ? { baseUrl: this.opts.baseUrl } : {}), apiKey: this.opts.apiKey, guard }),
      ]);
      if (j.recalled.length > 0) await deliver(sequencer.stamp({ type: "memory_recall", items: j.recalled }));
      const result = await runLoop(
        j.state,
        {
          provider: this.provider(j.state.model),
          tools,
          policy: createPolicy(it.risk, it.denyTools),
          approvals: this.approvals.requester,
          guard,
          prices: await this.prices(),
          now: () => this.now(),
          signal: controller.signal,
        },
        { sequencer, onEvent: deliver },
      );
      status = result.state.status;
      if (status === "done") {
        const last = [...result.state.messages].reverse().find((m) => m.role === "assistant");
        const id = await j.memory.recordTask({ taskId: j.taskId, goal: j.goal, outcome: typeof last?.content === "string" ? last.content.slice(0, 500) : "Finished.", ts: new Date(this.now()).toISOString() });
        if (id) await deliver(sequencer.stamp({ type: "memory_write", id, tier: "history" }));
      }
    } catch {
      status = controller.signal.aborted ? "cancelled" : "failed";
      await deliver(sequencer.stamp({ type: "run", status: status as "failed" | "cancelled", reason: controller.signal.aborted ? "Cancelled." : "The task stopped because of an internal error." }));
    } finally {
      this.aborts.delete(j.taskId);
      try {
        const endpoint = parseItPolicy(this.opts.policyJson).otlpEndpoint ?? (this.opts.otlpEndpoint || null);
        if (endpoint) await exportOtlp(this.opts.fetch, endpoint, collected, { serviceName: "open-computer-local" });
      } catch {
        /* telemetry is best effort */
      }
      this.store.setTaskStatus(j.taskId, status);
      await this.sandboxes.release(j.taskId).catch(() => undefined);
      this.closeSession(j.sessionId);
    }
  }

  decide(approvalId: string, decision: { hash: string; approved: boolean }) {
    return this.approvals.decide(approvalId, { ...decision, actor: "human" });
  }

  cancel(taskId: string): void {
    this.aborts.get(taskId)?.abort();
  }

  /** Waits for every running task; for tests and orderly shutdown. */
  async idle(): Promise<void> {
    await Promise.all([...this.running]);
  }

  dispose(): void {
    for (const c of this.aborts.values()) c.abort();
    this.approvals.dispose();
  }
}

/** A task the IT policy does not allow. The route turns it into a plain 403. */
export class PolicyRefusal extends Error {}
