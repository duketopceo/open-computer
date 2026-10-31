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

// JSON POST with a fresh CSRF token (the convention for every mutation).
async function postJson<T>(url: string, body: unknown): Promise<T> {
  const token = await csrf();
  return json<T>(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": token },
    body: JSON.stringify(body),
  });
}

// Poll cadence shared by every live view: fast while something is running, slow when idle or offline.
export function pollMs(link: string, active: boolean): number {
  return link === "offline" ? 10000 : active ? 2500 : 8000;
}

export async function submitGoal(
  goal: string,
  confirm: boolean,
): Promise<{ needs_confirm?: boolean; cap?: number; run?: string }> {
  return postJson("/goals", { goal, confirm: confirm ? "1" : undefined });
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
// --- Console pages (Devices, Sandboxes, Router, Audit) -------------------
// Conventions for every page port (see docs/plans/2026-10-30-*-console-react-unification-plan.md):
//   * reads are GET /api/<thing> returning an object (never a bare array);
//   * errors are {"error": "..."} with a 4xx/5xx status and surface as ApiError;
//   * writes are POST /api/<thing> with a JSON body and a fresh `X-CSRF-Token`
//     from csrf() (see submitGoal / testRoute);
//   * the server returns raw values; React's default escaping is the only
//     escaping, so never inject them as raw HTML.

export interface Device {
  device_id: string;
  os: string;
  capabilities: string[];
  roots: string[];
  state: "online" | "asleep" | "offline";
  last_heartbeat: number;
  active_grants: number;
  inbox_depth: number;
}

export interface DevicesPayload {
  devices: Device[];
  total: number;
  online: number;
  eligible: number;
  parked_depth: number;
}

export interface SandboxesPayload {
  configured: boolean;
  up: boolean;
  api_host: string;
  sandboxes: Record<string, unknown>[];
}

export interface LedgerRow {
  ts: number;
  event: string;
  route: string;
  engine: string;
  cost: number;
}

export interface RouterPayload {
  paid: boolean;
  dest_host: string;
  per_route_cost: number;
  spend: number;
  cap: number;
  ledger: LedgerRow[];
}

export interface RouteResult {
  route: string;
  tier: string;
  engine: string;
  confidence: number;
  cost: number;
  ms: number;
}

export type RouteTestResponse =
  | { needs_confirm: true; dest_host: string; cost: number }
  | { result: RouteResult };

export type AuditFilter = "all" | "blocked" | "allowed" | "decisions";

export interface AuditRow {
  ts: number;
  event: string;
  result: "blocked" | "allowed" | "approved" | "sent back" | "recorded";
  detail: string;
}

export interface AuditPayload {
  f: AuditFilter;
  q: string;
  total: number;
  shown: number;
  rows: AuditRow[];
}

export const getDevices = () => json<DevicesPayload>("/api/devices");
export const getSandboxes = () => json<SandboxesPayload>("/api/sandboxes");
export const getRouter = () => json<RouterPayload>("/api/router");

/** The `?f=&q=` suffix shared by the audit page URL and its API URL (defaults omitted). */
export function auditQuery(f: AuditFilter, q: string): string {
  const p = new URLSearchParams();
  if (f !== "all") p.set("f", f);
  if (q) p.set("q", q);
  const qs = p.toString();
  return qs ? `?${qs}` : "";
}

export const getAudit = (f: AuditFilter, q: string) => json<AuditPayload>(`/api/audit${auditQuery(f, q)}`);

export function testRoute(q: string, confirm: boolean): Promise<RouteTestResponse> {
  return postJson("/api/router/test", { q, confirm: confirm ? "1" : undefined });
}

// ---- Agent jobs (orchestrator console) ----

export type AgentStatus =
  | "queued"
  | "running"
  | "finished"
  | "stalled"
  | "rejected"
  | "budget_exhausted"
  | "max_rounds"
  | "failed";

export interface AgentJobSummary {
  job: string;
  goal: string;
  started: number;
  status: AgentStatus;
  budget: number;
  spent: number;
  awaiting_approval: boolean;
  pending: number;
}

export type ApprovalKind = "plan" | "risky" | "egress" | "spend" | "tool";

export interface Approval {
  id: string;
  kind: ApprovalKind;
  detail: Record<string, unknown>;
  hash: string;
  ts: number;
  expires_at: number;
}

export interface TrailRow {
  job: string;
  seq: number;
  ts: number;
  event: string;
  [field: string]: unknown;
}

export interface AgentJobDetail {
  job: string;
  goal: string;
  status: AgentStatus;
  budget: number;
  spent: number;
  events: TrailRow[];
  approvals: Approval[];
}

export async function listAgentJobs(): Promise<AgentJobSummary[]> {
  return (await json<{ jobs?: AgentJobSummary[] }>("/api/agent-jobs")).jobs ?? [];
}

export async function getAgentJob(id: string): Promise<AgentJobDetail> {
  return json<AgentJobDetail>(`/api/agent-jobs/${encodeURIComponent(id)}`);
}

export async function submitAgentJob(
  goal: string,
  budget: number | undefined,
  confirm: boolean,
): Promise<{ needs_confirm?: boolean; budget?: number; model?: string; job?: string }> {
  return postJson("/api/agent-jobs", { goal, budget, confirm: confirm ? "1" : undefined });
}

export async function decideApproval(
  job: string,
  id: string,
  verb: "approve" | "deny",
  hash: string,
  note?: string,
): Promise<{ ok: boolean }> {
  return postJson(`/api/agent-jobs/${encodeURIComponent(job)}/approvals/${encodeURIComponent(id)}/${verb}`, {
    hash,
    note,
  });
}

// ---- Approvals queue ----

export interface ApprovalItem {
  id: string;
  ts: number;
  age_s: number;
  status: string;
  kind?: string;
  payload?: Record<string, unknown>;
  payload_hash?: string;
  reason?: string;
  note?: string;
  decided_by?: string;
  decided_ts?: number;
}

export const getApprovals = () => json<{ approvals: ApprovalItem[] }>("/api/approvals");

export function decideQueueItem(
  id: string,
  verb: "approve" | "send-back",
  hash?: string,
  note?: string,
): Promise<{ ok: boolean }> {
  return postJson(`/api/approvals/${encodeURIComponent(id)}/${verb}`, { hash, note });
}

// ---- Overview ----

export interface OverviewPayload {
  pending_approvals: ApprovalItem[];
  runs: Record<string, unknown>[];
  audit: Record<string, unknown>[];
  spend: number;
  cap: number;
  now: number;
}

export const getOverview = () => json<OverviewPayload>("/api/overview");
