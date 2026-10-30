// Thin-shell API client. The SPA renders events and state from the opsview
// backend; it never runs agents or holds business data.

export interface Session {
  csrf: string;
  identity: string;
  cap: number;
}

export type TaskState =
  | "QUEUED"
  | "LEASED"
  | "RUNNING"
  | "DONE"
  | "FAILED"
  | "AWAITING-APPROVAL";

export interface RunSummary {
  run: string;
  goal: string;
  origin: string;
  device_id: string | null;
  subtasks: number;
  states: Record<TaskState, number>;
  ts: number;
}

export interface SubTask {
  subtask: string;
  title: string;
  state: TaskState;
  route: string;
  engine: string;
  placement: string;
  reason: string;
  gate_required: boolean;
  cost: number;
  pii: boolean;
  result?: string;
  error?: string;
  error_kind?: string;
  hint?: string;
  ts: number;
  started_ts?: number; // when a worker claimed it; absent for steps that never ran
  finished_ts?: number;
  thinking?: string; // reasoning text the step's model calls returned
}

export interface RunDetail {
  run: string;
  goal: string;
  origin: string;
  device_id: string | null;
  subtasks: SubTask[];
  declared: number;
  worker_failures: unknown[];
  started?: number;
  ended?: number;
  plan_thinking?: string;
  error?: string;
  error_kind?: string;
  hint?: string;
}

// auth: session gone (sign in again) · network: server unreachable ·
// server: 5xx / upstream · client: the request itself was refused (4xx).
export type ApiErrorKind = "auth" | "network" | "server" | "client";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly kind: ApiErrorKind,
    readonly status = 0,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function errorMessage(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

async function request(url: string, init?: RequestInit): Promise<unknown> {
  let res: Response;
  try {
    res = await fetch(url, {
      ...init,
      credentials: "same-origin",
      headers: { Accept: "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    // Also what an expired Cloudflare Access session looks like: the gate's
    // cross-origin login redirect fails CORS and surfaces as a network error.
    throw new ApiError("Can't reach the server. Check your connection, or reload to sign in again.", "network");
  }
  const isJson = (res.headers.get("Content-Type") ?? "").includes("application/json");
  // An unauthenticated GET is redirected to the HTML /login page.
  if (res.status === 401 || (res.ok && !isJson && (res.redirected || res.url.includes("/login")))) {
    throw new ApiError("Your session expired. Reload to sign in again.", "auth", res.status);
  }
  const body = (isJson ? await res.json().catch(() => null) : null) as { error?: string } | null;
  if (!res.ok) {
    const msg = body?.error ?? `Request failed (HTTP ${res.status}).`;
    throw new ApiError(msg, res.status >= 500 ? "server" : "client", res.status);
  }
  if (body === null) throw new ApiError("The server sent an unexpected response.", "server", res.status);
  return body;
}

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  return (await request(url, init)) as T;
}

let sessionPromise: Promise<Session> | null = null;

export function session(): Promise<Session> {
  if (!sessionPromise) {
    sessionPromise = json<Session>("/api/session").catch((e) => {
      sessionPromise = null; // a failed fetch must not poison later retries
      throw e;
    });
  }
  return sessionPromise;
}

// CSRF is bound to the session cookie, which can be re-minted (Access,
// loopback) mid-session. Fetch it fresh before each mutation rather than
// caching it once and going stale.
export async function csrf(): Promise<string> {
  const s = await json<Session>("/api/session");
  sessionPromise = Promise.resolve(s);
  return s.csrf;
}

export async function listRuns(): Promise<RunSummary[]> {
  return (await json<{ runs?: RunSummary[] }>("/api/runs")).runs ?? [];
}

export async function getRun(id: string): Promise<RunDetail> {
  return json<RunDetail>(`/api/runs/${encodeURIComponent(id)}`);
}

export async function submitGoal(
  goal: string,
  confirm: boolean,
): Promise<{ needs_confirm?: boolean; cap?: number; run?: string }> {
  const token = await csrf();
  return json("/goals", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": token },
    body: JSON.stringify({ goal, confirm: confirm ? "1" : undefined }),
  });
}

export async function transcribe(audio: Blob): Promise<string> {
  const token = await csrf();
  const form = new FormData();
  form.append("file", audio, "utterance.webm");
  form.append("model", "whisper-large-v3-turbo");
  const body = await json<{ text?: string }>("/api/transcribe", {
    method: "POST",
    headers: { "X-CSRF-Token": token },
    body: form,
  });
  const text = (body.text ?? "").trim();
  if (!text) throw new ApiError("Didn't catch any speech — try again.", "client");
  return text;
}