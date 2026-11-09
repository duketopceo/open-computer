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
  archived?: boolean;
  /** Nothing left that could still progress, and nothing is driving it: safe to remove. */
  finished?: boolean;
}

/** The router's decision for a step; `fallbacks` lists the engines tried first and why each was skipped. */
export interface RouterInfo {
  engine: string;
  model?: string | null;
  confidence?: number | null;
  ms?: number | null;
  fallbacks?: { engine: string; kind: string; status?: number | null; reason?: string }[];
}

/** What actually served a step: the model id the provider reported, and its tokens and dollars. */
export interface WorkerInfo {
  model: string;
  provider?: string | null;
  tokens_in?: number;
  tokens_out?: number;
  tokens_reasoning?: number;
  cost?: number;
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
  /** PII categories the gate found in the step's prompt; empty means none. */
  pii?: string[] | null;
  result?: string;
  error?: string;
  error_kind?: string;
  hint?: string;
  ts: number;
  started_ts?: number; // when a worker claimed it; absent for steps that never ran
  finished_ts?: number;
  skipped?: boolean; // a dependency failed, so the step never ran
  thinking?: string; // reasoning text the step's model calls returned
  router?: RouterInfo; // absent on runs recorded before the router decision was kept
  worker?: WorkerInfo; // absent for steps that made no model call, and on older runs
  waiting_on?: WaitingOn; // a requeued step that is backing off before its next attempt
  approval?: string; // AWAITING-APPROVAL steps: the Approvals queue item that unblocks them
  hosts?: string[]; // ...and the sites that item asks to allow
}

// Why a step is queued again: `attempt` executions have run so far, out of `max` allowed.
export interface WaitingOn {
  kind: string;
  attempt: number;
  max: number;
  since: number;
  detail?: string;
}

// What a run was started with, and what it has spent against its own budget.
// `paused`: the budget stopped new steps; nothing was cancelled.
export interface RunEffort {
  id: string | null;
  model: string | null;
  budget: number | null;
  spent: number;
  paused: boolean;
}

export interface PlanStep {
  id: string;
  title: string;
  prompt: string;
  deps?: string[];
  tier?: string | null;
  est_cost?: number | null;
  needs_egress?: boolean;
  egress_hosts?: string[];
}

// A plan the run holds for the owner: nothing runs while `state` is "planned".
export interface RunPlan {
  state: "planned" | "approved" | "cancelled";
  estimate: number;
  budget: number | null;
  model: string | null;
  steps: PlanStep[];
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
  plan?: RunPlan;
  effort?: RunEffort;
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

export async function json<T>(url: string, init?: RequestInit): Promise<T> {
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

export async function listRuns(withArchived = false, looseOnly = false): Promise<RunSummary[]> {
  const q = [withArchived && "archived=1", looseOnly && "threaded=0"].filter(Boolean).join("&");
  return (await json<{ runs?: RunSummary[] }>("/api/runs" + (q ? "?" + q : ""))).runs ?? [];
}

// ---- chat threads
export interface ThreadSummary {
  id: string;
  title: string;
  ts: number;
  archived: boolean;
  working: boolean;
  finished: boolean;
}

export interface ThreadMessage {
  role: "user" | "assistant" | "run";
  text: string;
  ts: number;
  run?: string;
  goal?: string;
  ask?: boolean;
  error?: boolean;
}

export interface ThreadDetail extends Omit<ThreadSummary, "ts" | "finished"> {
  created: number;
  updated: number;
  effort: { effort: string; model: string; budget: number };
  messages: ThreadMessage[];
  runs: Record<string, RunDetail | null>;
  /** Present only when memory is on: what each turn recalled and what the thread stored. */
  memory?: ThreadMemory;
}

/** One Kurultai note as the console sees it: id and title, never the text. */
export interface MemoryAtom {
  id: string;
  title: string;
  source?: string;
  score?: number;
  forgotten?: boolean;
}

/** The notes one user message's turn recalled; `user_ts` is that message's `ts`. */
export interface MemoryRecall {
  user_ts: number | null;
  ts: number;
  withheld: number;
  atoms: MemoryAtom[];
}

export interface ThreadMemory {
  recalls: MemoryRecall[];
  writes: { ts: number; atom: string; title: string; run?: string | null; forgotten: boolean }[];
}

export interface MemoryStatus {
  enabled: boolean;
  project: string;
  url?: string;
  limit?: number;
  include_quarantine?: boolean;
  token?: boolean;
  counts: { recalls: number; writes: number; withheld: number; forgotten: number; errors: number };
  last_error: { ts: number; op: string } | null;
  recent_writes: { atom: string; title: string; thread?: string; ts: number }[];
}


export interface RuntimeStep {
  id: string;
  title: string;
  where: string;
  detail: string;
}

export interface RuntimeStatus {
  summary: string;
  steps: RuntimeStep[];
  docs: string;
}

export interface BrainStatus {
  url: string | null;
  health: "unset" | "up" | "down";
  hint: string | null;
}

export const getRuntime = () => json<RuntimeStatus>("/api/runtime");
export const getBrain = () => json<BrainStatus>("/api/brain");

export const getMemory = () => json<MemoryStatus>("/api/memory");
export const forgetMemory = (atom: string, thread?: string) =>
  postJson<{ ok: true; atom: string }>("/api/memory/forget", thread ? { atom, thread } : { atom });

export async function listThreads(withArchived = true): Promise<ThreadSummary[]> {
  return (await json<{ threads?: ThreadSummary[] }>(withArchived ? "/api/threads?archived=1" : "/api/threads")).threads ?? [];
}
export const getThread = (id: string) => json<ThreadDetail>(`/api/threads/${encodeURIComponent(id)}`);
export const createThread = (text: string, opts: GoalOptions = {}) =>
  postJson<{ thread: string }>("/api/threads", { text, ...opts });
export const sendMessage = (id: string, text: string) =>
  postJson<{ ok: true }>(`/api/threads/${encodeURIComponent(id)}/messages`, { text });
export const deleteThread = (id: string) => postJson<{ ok: true }>(`/api/threads/${encodeURIComponent(id)}/delete`, {});
export const archiveThread = (id: string, archived: boolean) =>
  postJson<{ ok: true }>(`/api/threads/${encodeURIComponent(id)}/archive`, { archived });
export const clearFinishedThreads = () => postJson<{ cleared: number }>("/api/threads/clear", { confirm: true });

export const deleteRun = (id: string) => postJson<{ ok: true }>(`/api/runs/${encodeURIComponent(id)}/delete`, {});
export const archiveRun = (id: string, archived: boolean) =>
  postJson<{ ok: true }>(`/api/runs/${encodeURIComponent(id)}/archive`, { archived });
export const clearFinishedRuns = () => postJson<{ cleared: number }>("/api/runs/clear", { confirm: true });

export async function getRun(id: string): Promise<RunDetail> {
  return json<RunDetail>(`/api/runs/${encodeURIComponent(id)}`);
}

// ---- run files (artifacts): derived from the run's results, listed without bodies
export interface ArtifactItem {
  name: string;
  step: string;
  size?: number;
  mime?: string;
  /** over the size limit: listed, never saved */
  refused?: string;
}
export const getArtifacts = async (id: string) =>
  (await json<{ artifacts?: ArtifactItem[] }>(`/api/runs/${encodeURIComponent(id)}/artifacts`)).artifacts ?? [];
export const artifactUrl = (id: string, name: string, download = false) =>
  name === "files.zip"
    ? `/api/runs/${encodeURIComponent(id)}/artifacts.zip`
    : `/api/runs/${encodeURIComponent(id)}/artifacts/${encodeURIComponent(name)}${download ? "?download=1" : ""}`;
export async function getArtifactText(id: string, name: string): Promise<string> {
  const r = await fetch(artifactUrl(id, name), { credentials: "same-origin" });
  if (!r.ok) throw new ApiError("file unavailable", "client", r.status);
  return r.text();
}

export const decidePlan = (id: string, action: "approve" | "cancel", edits: Record<string, unknown>[] = []) =>
  postJson<{ state: string }>(`/api/runs/${encodeURIComponent(id)}/plan`, { action, edits });

// JSON POST with a fresh CSRF token (the convention for every mutation).
export async function postJson<T>(url: string, body: unknown): Promise<T> {
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

// The effort picker's options come from the server so the UI never carries its own model or price list.
export interface EffortLevel {
  id: string;
  label: string;
  blurb: string;
  model: string;
  fallbacks?: string[]; // the level's two other models, in the order OpenRouter tries them
  budget: number;
}

export interface EffortCatalog {
  default: string;
  max_budget: number;
  models: string[];
  levels: EffortLevel[];
}

export function getEffort(): Promise<EffortCatalog> {
  return json<EffortCatalog>("/api/effort");
}

// `model` and `budget` are the advanced overrides; omitted means "what the effort level says".
export interface GoalOptions {
  effort?: string;
  model?: string;
  budget?: number;
}

export interface GoalReply {
  needs_confirm?: boolean;
  cap?: number;
  run?: string;
  effort?: string;
  model?: string;
  budget?: number;
}

export async function submitGoal(goal: string, confirm: boolean, opts: GoalOptions = {}): Promise<GoalReply> {
  return postJson("/goals", { goal, ...opts, confirm: confirm ? "1" : undefined });
}

export interface VoiceStatus {
  available: boolean;
  reason?: string;
}

export async function voiceStatus(): Promise<VoiceStatus> {
  return json<VoiceStatus>("/api/voice");
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
  /** Who asked (agent name) and the session it ran in. */
  who?: string;
  session?: string;
  /** What the agent wants to send, in its own words. */
  what?: string;
  model?: string;
  /** Destination host the payload leaves this box for. */
  egress?: string;
  est_cost?: number;
  /** PII categories the gate found in the payload; empty when clean. */
  pii?: string[];
  payload_hash?: string;
  payload_bytes?: number;
  payload?: Record<string, unknown>;
  note?: string;
  decided_by?: string;
  decided_ts?: number;
}

/** Same rule the old server-rendered page used: PII is high, real spend is elevated. */
export function approvalRisk(i: ApprovalItem): { level: "high" | "elevated" | "low"; why: string } {
  if (i.pii && i.pii.length) return { level: "high", why: "PII: " + i.pii.join(", ") };
  if ((i.est_cost ?? 0) >= 0.05) return { level: "elevated", why: "High spend" };
  return { level: "low", why: "No PII, within budget" };
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

// ---- Skills + Automations ----

export interface Skill { [k: string]: unknown }
export interface Automation {
  id: string;
  goal: string;
  every_minutes: number;
  paused: boolean;
  next_run?: number;
  last_run?: number;
  state?: string;
}

export const getSkills = () => json<{ skills: Skill[] }>("/api/skills");
export const getAutomations = () =>
  json<{ enabled: boolean; automations: Automation[] }>("/api/automations");
export const autoAction = (id: string, verb: "pause" | "trigger" | "delete") =>
  postJson(`/automations/${encodeURIComponent(id)}/${verb}`, {});
export const newAutomation = (goal: string, every_minutes: number) =>
  postJson("/automations", { goal, every_minutes });

// ---- OpenRouter connection (OAuth PKCE; the server holds the verifier and stores the key in omaseal)
export interface OpenRouterStatus {
  connected: boolean;
  source: "omaseal" | "env" | null;
}
export const getOpenRouter = () => json<OpenRouterStatus>("/api/openrouter");
export const startOpenRouterConnect = () =>
  postJson<{ url: string }>("/api/openrouter/connect", { origin: window.location.origin });
export const completeOpenRouterConnect = (code: string, state: string) =>
  postJson<OpenRouterStatus>("/api/openrouter/complete", { code, state });
export const disconnectOpenRouter = () => postJson<OpenRouterStatus>("/api/openrouter/disconnect", {});
