import { SELF, adminSecretsStore } from "cloudflare:test";
import { env } from "./env.ts";
import type { AgentEvent, ChatMessage, Provider, ProviderRequest, StreamPart, Usage } from "@open-computer/agent-core";
import { setDoubles } from "./entry.ts";
import type { ExecResult, SandboxPort } from "../src/sandbox/port.ts";

// Key-shaped values are assembled at run time so no line looks like a real provider key to a secret scanner.
export const fakeOpenRouterKey = (tail = "abcdef1234567890"): string => ["sk", "or", "v1", tail].join("-");
export const fakeGenericKey = (): string => ["sk", "test", "1234567890"].join("-");

export const TOKEN = "test-access-secret-0123456789abcdef";
export const MODEL_KEY = "test-model-key-ABCDEF0123456789";

export async function seedSecrets(): Promise<void> {
  for (const [binding, value] of [
    [env.ACCESS_SECRET, TOKEN],
    [env.MODEL_KEY, MODEL_KEY],
  ] as const) {
    const admin = adminSecretsStore(binding);
    try {
      await admin.create(value);
    } catch {
      /* already present in this store */
    }
    admin[Symbol.dispose]();
  }
}

let ipCounter = 0;
export function api(path: string, init: RequestInit & { token?: string | null; ip?: string } = {}): Promise<Response> {
  const { token = TOKEN, ip, ...rest } = init;
  const headers = new Headers(rest.headers);
  if (token) headers.set("authorization", `Bearer ${token}`);
  headers.set("cf-connecting-ip", ip ?? `203.0.113.${(ipCounter++ % 250) + 1}`);
  if (rest.body && !headers.has("content-type")) headers.set("content-type", "application/json");
  return SELF.fetch(`https://core.test${path}`, { ...rest, headers });
}

export const usage = (cost = 0.001): Usage => ({ prompt: 10, completion: 5, reasoning: 0, cached: 0, cost });

export function textTurn(text: string, u: Usage = usage(), generationId: string | null = null): StreamPart[] {
  return [
    { t: "text", text },
    { t: "done", message: { role: "assistant", content: text }, finishReason: "stop", usage: u, generationId, reasoningKinds: [] },
  ];
}

export function toolTurn(calls: Array<{ id: string; name: string; args: string }>): StreamPart[] {
  const parts: StreamPart[] = [];
  calls.forEach((c, index) => {
    parts.push({ t: "tool_start", index, call_id: c.id, name: c.name });
    parts.push({ t: "tool_args", call_id: c.id, text: c.args });
  });
  parts.push({
    t: "done",
    message: { role: "assistant", content: null, tool_calls: calls.map((c) => ({ id: c.id, name: c.name, arguments: c.args })) },
    finishReason: "tool_calls",
    usage: usage(),
    reasoningKinds: [],
  });
  return parts;
}

export interface Script {
  /** One entry per model call, played in order. A function turn can throw to simulate a mid-stream failure. */
  turns: Array<StreamPart[] | ((attempt: number) => StreamPart[] | { partial: StreamPart[]; throwAfter: Error; waitFor?: Promise<void> })>;
  requests: ProviderRequest[];
  calls: number;
  /** When set, the provider waits for it before streaming, which holds a step in flight. */
  gate?: Promise<void>;
}

export function scriptedProvider(script: Script): Provider {
  return async function* (request) {
    script.requests.push(JSON.parse(JSON.stringify(request)) as ProviderRequest);
    const index = Math.min(script.calls, script.turns.length - 1);
    script.calls += 1;
    if (script.gate) await script.gate;
    const entry = script.turns[index];
    const turn = typeof entry === "function" ? entry(script.calls) : entry;
    if (!turn) return;
    if (!Array.isArray(turn)) {
      for (const p of turn.partial) yield p;
      if (turn.waitFor) await turn.waitFor;
      throw turn.throwAfter;
    }
    for (const p of turn) yield p;
  };
}

export class FakeSandbox implements SandboxPort {
  readonly execs: string[] = [];
  destroyed = false;
  async exec(command: string): Promise<ExecResult> {
    this.execs.push(command);
    return { stdout: "a.txt\nb.txt\n", stderr: "", exitCode: 0 };
  }
  async readFile(path: string): Promise<string> {
    return `contents of ${path}`;
  }
  async writeFile(): Promise<void> {}
  async destroy(): Promise<void> {
    this.destroyed = true;
  }
}

export interface Fixture {
  script: Script;
  sandboxes: Map<string, FakeSandbox>;
  fetchCalls: Array<{ url: string; auth: string | null }>;
  keyInfoStatus: { value: number };
  failFlushAt: string[];
  /** Sets the IT policy text the handler sees for the rest of the test (undefined clears it). */
  setPolicy: (text: string | undefined) => void;
}

export function install(turns: Script["turns"]): Fixture {
  const script: Script = { turns, requests: [], calls: 0 };
  const sandboxes = new Map<string, FakeSandbox>();
  const fetchCalls: Fixture["fetchCalls"] = [];
  const keyInfoStatus = { value: 200 };
  const failFlushAt: string[] = [];
  const current: Parameters<typeof setDoubles>[0] = {
    failFlushAt,
    provider: scriptedProvider(script),
    sandbox: (taskId) => {
      const sb = new FakeSandbox();
      sandboxes.set(taskId, sb);
      return sb;
    },
    fetch: async (url, init) => {
      fetchCalls.push({ url, auth: new Headers(init.headers).get("authorization") });
      return new Response("{}", { status: keyInfoStatus.value });
    },
  };
  setDoubles(current);
  return { script, sandboxes, fetchCalls, keyInfoStatus, failFlushAt, setPolicy: (text) => void (current.policyJson = text) };
}

export interface SseResult {
  events: AgentEvent[];
  /** Each supersede frame received: the inclusive range of sequence numbers it replaced. */
  superseded: Array<{ from_seq: number; to_seq: number }>;
  /** The id of the last frame of any kind, for Last-Event-ID. */
  lastId: number;
}

/** Applies one SSE chunk to a result: events are kept, a supersede frame drops the range it names. */
export function applySse(into: SseResult, text: string): SseResult {
  for (const block of text.split("\n\n")) {
    const lines = block.split("\n");
    const name = lines.find((l) => l.startsWith("event:"))?.slice(6).trim();
    const id = lines.find((l) => l.startsWith("id:"))?.slice(3).trim();
    const data = lines.find((l) => l.startsWith("data:"))?.slice(5).trim();
    if (!data) continue;
    if (id !== undefined) into.lastId = Number(id);
    if (name === "supersede") {
      const range = JSON.parse(data) as { from_seq: number; to_seq: number };
      into.superseded.push(range);
      into.events = into.events.filter((e) => e.seq < range.from_seq || e.seq > range.to_seq);
    } else {
      into.events.push(JSON.parse(data) as AgentEvent);
    }
  }
  return into;
}

/** Reads an SSE response to its end and applies the client-side supersede rule. */
export async function readSse(res: Response): Promise<SseResult> {
  return applySse({ events: [], superseded: [], lastId: -1 }, await res.text());
}

/** Reads an SSE response until `done` says enough has arrived, then disconnects, as a client that drops would. */
export async function readSseUntil(res: Response, done: (r: SseResult) => boolean): Promise<SseResult> {
  const result: SseResult = { events: [], superseded: [], lastId: -1 };
  const reader = (res.body as ReadableStream<Uint8Array>).getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done: end } = await reader.read();
    if (value) buffer += decoder.decode(value, { stream: true });
    const cut = buffer.lastIndexOf("\n\n");
    if (cut >= 0) {
      applySse(result, buffer.slice(0, cut + 2));
      buffer = buffer.slice(cut + 2);
    }
    if (end || done(result)) break;
  }
  await reader.cancel();
  return result;
}

export async function startTask(body: Record<string, unknown>): Promise<{ task_id: string; session_id: string; day_cap: number }> {
  const res = await api("/v1/tasks", { method: "POST", body: JSON.stringify(body) });
  if (res.status !== 202) throw new Error(`task not accepted: ${res.status} ${await res.text()}`);
  return (await res.json()) as { task_id: string; session_id: string; day_cap: number };
}

export async function waitFor<T>(fn: () => Promise<T | undefined>, what: string, tries = 300): Promise<T> {
  for (let i = 0; i < tries; i++) {
    const v = await fn();
    if (v !== undefined) return v;
    await new Promise((r) => setTimeout(r, 20));
  }
  throw new Error(`timed out waiting for ${what}`);
}

export const lastToolMessage = (messages: ChatMessage[]) => messages.at(-1);
