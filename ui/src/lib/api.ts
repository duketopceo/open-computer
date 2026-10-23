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
  finished_ts?: number;
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
  error?: string;
  error_kind?: string;
  hint?: string;
}

let sessionPromise: Promise<Session> | null = null;

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    credentials: "same-origin",
    headers: { Accept: "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error((body as { error?: string }).error ?? `HTTP ${res.status}`);
  }
  return body as T;
}

export function session(): Promise<Session> {
  if (!sessionPromise) sessionPromise = json<Session>("/api/session");
  return sessionPromise;
}

// The server derives CSRF from a per-process secret + the session cookie, so
// it rotates on every restart. Fetch it fresh before each mutation rather than
// caching it once and going stale mid-session.
export async function csrf(): Promise<string> {
  const s = await json<Session>("/api/session");
  sessionPromise = Promise.resolve(s);
  return s.csrf;
}

export async function listRuns(): Promise<RunSummary[]> {
  return (await json<{ runs: RunSummary[] }>("/api/runs")).runs;
}

export async function getRun(id: string): Promise<RunDetail> {
  return json<RunDetail>(`/api/runs/${id}`);
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
  const res = await fetch("/api/transcribe", {
    method: "POST",
    credentials: "same-origin",
    headers: { "X-CSRF-Token": token },
    body: form,
  });
  const body = (await res.json().catch(() => ({}))) as { text?: string; error?: string };
  if (!res.ok) throw new Error(body.error ?? `HTTP ${res.status}`);
  return body.text ?? "";
}