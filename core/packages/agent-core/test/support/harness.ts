import {
  ApprovalQueue,
  CostGuard,
  MemoryLedger,
  ToolRegistry,
  createPolicy,
  createState,
  staticPriceBook,
  type AgentDeps,
  type AgentState,
  type Policy,
  type Provider,
  type ProviderRequest,
  type StreamPart,
  type Tool,
  type Usage,
} from "../../src/index.ts";

export const usage = (cost: number | null = 0.001, over: Partial<Usage> = {}): Usage => ({
  prompt: 10,
  completion: 5,
  reasoning: 0,
  cached: 0,
  cost,
  ...over,
});

export function textTurn(text: string, u: Usage | null = usage()): StreamPart[] {
  return [
    { t: "text", text },
    { t: "done", message: { role: "assistant", content: text }, finishReason: "stop", usage: u, reasoningKinds: [] },
  ];
}

export function toolTurn(calls: Array<{ id: string; name: string; args: string }>, u: Usage | null = usage()): StreamPart[] {
  const parts: StreamPart[] = [];
  calls.forEach((c, index) => {
    parts.push({ t: "tool_start", index, call_id: c.id, name: c.name });
    parts.push({ t: "tool_args", call_id: c.id, text: c.args });
  });
  parts.push({
    t: "done",
    message: {
      role: "assistant",
      content: null,
      tool_calls: calls.map((c) => ({ id: c.id, name: c.name, arguments: c.args })),
      reasoning_details: [{ type: "reasoning.encrypted", data: "REPLAY", index: 0 }],
    },
    finishReason: "tool_calls",
    usage: u,
    reasoningKinds: [],
  });
  return parts;
}

/** A provider that plays one scripted turn per call and records every request it received. */
export function scriptedProvider(turns: StreamPart[][]): { provider: Provider; requests: ProviderRequest[] } {
  const requests: ProviderRequest[] = [];
  let i = 0;
  const provider: Provider = async function* (request, signal) {
    requests.push(JSON.parse(JSON.stringify(request)) as ProviderRequest);
    const turn = turns[Math.min(i, turns.length - 1)] ?? [];
    i += 1;
    for (const part of turn) {
      if (signal?.aborted) return;
      yield part;
      await Promise.resolve();
    }
  };
  return { provider, requests };
}

export function echoTool(name = "echo", risk: Tool["spec"]["risk"] = "read", fn?: (args: unknown) => string): Tool {
  return {
    spec: { name, description: name, parameters: { type: "object" }, risk },
    run: async (args) => (fn ? fn(args) : `ran ${name} ${JSON.stringify(args)}`),
  };
}

export interface Harness {
  deps: Omit<AgentDeps, "emit">;
  queue: ApprovalQueue;
  ledger: MemoryLedger;
  guard: CostGuard;
  state: AgentState;
  requests: ProviderRequest[];
}

export function makeHarness(opts: {
  turns: StreamPart[][];
  tools?: Tool[];
  policy?: Policy;
  taskCap?: number;
  dayCap?: number | null;
  prices?: Record<string, { inPerMTok: number; outPerMTok: number }>;
  limits?: Parameters<typeof createState>[0]["limits"];
  signal?: AbortSignal;
  now?: () => number;
}): Harness {
  const { provider, requests } = scriptedProvider(opts.turns);
  const queue = new ApprovalQueue({ ttlMs: 60_000 });
  const ledger = new MemoryLedger();
  const now = opts.now ?? (() => 1_000_000);
  const guard = new CostGuard(ledger, { taskCap: opts.taskCap ?? 100, dayCap: opts.dayCap ?? null, now });
  const deps: Omit<AgentDeps, "emit"> = {
    provider,
    tools: new ToolRegistry(opts.tools ?? []),
    policy: opts.policy ?? createPolicy({ read: "allow", write: "allow", network: "allow", exec: "allow" }),
    approvals: queue.requester,
    guard,
    prices: staticPriceBook(opts.prices ?? { "example/model": { inPerMTok: 0, outPerMTok: 0 } }),
    now,
    ...(opts.signal ? { signal: opts.signal } : {}),
  };
  const state = createState({
    taskId: "task-1",
    model: "example/model",
    goal: "do the thing",
    startedAtMs: now(),
    ...(opts.limits ? { limits: opts.limits } : {}),
  });
  return { deps, queue, ledger, guard, state, requests };
}
