import { SELF, adminSecretsStore } from "cloudflare:test";
import { env } from "./env.ts";
import type { AgentEvent, ChatMessage, Provider, ProviderRequest, StreamPart, Usage } from "@open-computer/agent-core";
import { setDoubles } from "./entry.ts";
import type { ExecResult, SandboxPort } from "../src/sandbox/port.ts";

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

export function textTurn(text: string, u: Usage = usage()): StreamPart[] {
  return [
    { t: "text", text },
    { t: "done", message: { role: "assistant", content: text }, finishReason: "stop", usage: u, reasoningKinds: [] },
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
  turns: Array<StreamPart[] | ((attempt: number) => StreamPart[] | { partial: StreamPart[]; throwAfter: Error })>;
  requests: ProviderRequest[];
  calls: number;
}

export function scriptedProvider(script: Script): Provider {
  return async function* (request) {
    script.requests.push(JSON.parse(JSON.stringify(request)) as ProviderRequest);
    const index = Math.min(script.calls, script.turns.length - 1);
    script.calls += 1;
    const entry = script.turns[index];
    const turn = typeof entry === "function" ? entry(script.calls) : entry;
    if (!turn) return;
    if (!Array.isArray(turn)) {
      for (const p of turn.partial) yield p;
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
}

export function install(turns: Script["turns"]): Fixture {
  const script: Script = { turns, requests: [], calls: 0 };
  const sandboxes = new Map<string, FakeSandbox>();
  const fetchCalls: Fixture["fetchCalls"] = [];
  const keyInfoStatus = { value: 200 };
  setDoubles({
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
  });
  return { script, sandboxes, fetchCalls, keyInfoStatus };
}

/** Reads an SSE response to its end and applies the client-side supersede rule. */
export async function readSse(res: Response): Promise<{ events: AgentEvent[]; superseded: number[] }> {
  const text = await res.text();
  const events: AgentEvent[] = [];
  const superseded: number[] = [];
  for (const block of text.split("\n\n")) {
    const lines = block.split("\n");
    const name = lines.find((l) => l.startsWith("event:"))?.slice(6).trim();
    const data = lines.find((l) => l.startsWith("data:"))?.slice(5).trim();
    if (!data) continue;
    if (name === "supersede") {
      const from = (JSON.parse(data) as { from_seq: number }).from_seq;
      superseded.push(from);
      while (events.length && (events.at(-1) as AgentEvent).seq >= from) events.pop();
    } else {
      events.push(JSON.parse(data) as AgentEvent);
    }
  }
  return { events, superseded };
}

export async function startTask(body: Record<string, unknown>): Promise<{ task_id: string; session_id: string }> {
  const res = await api("/v1/tasks", { method: "POST", body: JSON.stringify(body) });
  if (res.status !== 202) throw new Error(`task not accepted: ${res.status} ${await res.text()}`);
  return (await res.json()) as { task_id: string; session_id: string };
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
