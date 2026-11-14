// Demo fixtures: plainly fictional sample data, typed with the real api.ts interfaces so a schema change fails `tsc`.
// Nothing here is real: example hosts, a made-up visitor, invented runs. No keys, tokens or personal data.
import type {
  Account,
  ApprovalItem,
  AuditRow,
  AgentJobSummary,
  ConnectorsStatus,
  DevicesPayload,
  EffortCatalog,
  OverviewPayload,
  RouterPayload,
  RunDetail,
  RuntimeStatus,
  SandboxesPayload,
  Session,
  SubTask,
  ThreadDetail,
} from "@/lib/api";

export const DEMO_MARKER = "oc-demo-fixtures-v1";

const MIN = 60;

export const session = (): Session => ({ csrf: "demo", identity: "demo@example.com", cap: 1 });

export const account = (): Account => ({
  id: "acct_demo",
  email: "demo@example.com",
  name: "Demo visitor",
  created: Math.floor(Date.now() / 1000) - 3 * 86400,
  via: "email",
  signout: "",
});

export const effort = (): EffortCatalog => ({
  default: "balanced",
  max_budget: 2,
  models: ["example/fast-1", "example/balanced-1", "example/deep-1"],
  levels: [
    { id: "quick", label: "Quick", blurb: "Small, cheap model for short tasks.", model: "example/fast-1", budget: 0.05 },
    { id: "balanced", label: "Balanced", blurb: "A mid-size model for most work.", model: "example/balanced-1", budget: 0.25 },
    { id: "deep", label: "Deep", blurb: "A larger model for hard, multi-step work.", model: "example/deep-1", budget: 1 },
  ],
});

export const runtime = (): RuntimeStatus => ({
  summary: "Demo: sample data only. In the real console, steps run in disposable sandboxes you own.",
  steps: [
    { id: "plan", title: "Plan", where: "demo", detail: "A planner splits the request into steps." },
    { id: "route", title: "Route", where: "demo", detail: "Each step goes to the cheapest model that fits." },
    { id: "run", title: "Run", where: "demo", detail: "Steps run in a sandbox with outbound traffic denied by default." },
  ],
  docs: "",
});

function step(n: number, at: number, p: Partial<SubTask> & Pick<SubTask, "title" | "state">): SubTask {
  return {
    subtask: `st_demo${n}`,
    route: "work",
    engine: "example/balanced-1",
    placement: "cloud",
    reason: "Cheapest model that fits the step.",
    gate_required: false,
    cost: 0,
    ts: at,
    started_ts: at,
    finished_ts: at + 9,
    worker: { model: "example/balanced-1", provider: "example", tokens_in: 820, tokens_out: 240, cost: 0.0012 },
    router: { engine: "example/balanced-1", model: "example/balanced-1", confidence: 0.91, ms: 120 },
    ...p,
  };
}

export function runs(now: number): Record<string, RunDetail> {
  const t0 = now - 50 * MIN;
  const done: RunDetail = {
    run: "goal_demo0001",
    goal: "Summarise the three vendor contracts and list renewal dates",
    origin: "thread",
    device_id: null,
    declared: 4,
    worker_failures: [],
    started: t0,
    ended: t0 + 80,
    effort: { id: "balanced", model: "example/balanced-1", budget: 0.25, spent: 0.0141, paused: false },
    subtasks: [
      step(1, t0, { title: "Read the contracts", state: "DONE", cost: 0.0031, result: "Found 3 contracts: Acme Hosting, Northwind Support, Globex Data." }),
      step(2, t0 + 12, { title: "Extract renewal dates", state: "DONE", cost: 0.0042, result: "Acme 2027-02-01 · Northwind 2026-12-15 · Globex 2027-06-30" }),
      step(3, t0 + 30, { title: "Flag notice periods", state: "DONE", cost: 0.0038, result: "Northwind needs 60 days notice, so decide by 2026-10-16." }),
      step(4, t0 + 55, { title: "Write the summary", state: "DONE", cost: 0.0030, result: "One page, three sections, one action item." }),
    ],
  };
  const waiting: RunDetail = {
    run: "goal_demo0002",
    goal: "Draft a reply to the customer with the invoice figures",
    origin: "thread",
    device_id: null,
    declared: 3,
    worker_failures: [],
    started: now - 6 * MIN,
    effort: { id: "balanced", model: "example/balanced-1", budget: 0.25, spent: 0.004, paused: false },
    subtasks: [
      step(5, now - 6 * MIN, { title: "Collect the invoice figures", state: "DONE", cost: 0.004, result: "Invoice 1042: 3 line items, total 1,240.00." }),
      step(6, now - 4 * MIN, {
        title: "Send the figures to a cloud model",
        state: "AWAITING-APPROVAL",
        gate_required: true,
        pii: ["email"],
        approval: "ap_demo_1",
        hosts: ["api.example-models.test"],
        finished_ts: undefined,
      }),
      step(7, now - 4 * MIN, { title: "Draft the reply", state: "QUEUED", finished_ts: undefined, started_ts: undefined }),
    ],
  };
  const failed: RunDetail = {
    run: "goal_demo0003",
    goal: "Check the status page for the payments provider",
    origin: "thread",
    device_id: null,
    declared: 2,
    worker_failures: [],
    started: now - 3 * 3600,
    ended: now - 3 * 3600 + 40,
    effort: { id: "quick", model: "example/fast-1", budget: 0.05, spent: 0.0004, paused: false },
    subtasks: [
      step(8, now - 3 * 3600, {
        title: "Open the status page",
        state: "FAILED",
        error: "The sandbox could not reach status.example.test: outbound traffic is denied for this step.",
        error_kind: "egress_denied",
        hint: "Allow the host for this step, or paste the status text into the chat.",
      }),
      step(9, now - 3 * 3600 + 20, { title: "Summarise the status", state: "QUEUED", skipped: true, started_ts: undefined, finished_ts: undefined }),
    ],
  };
  return { [done.run]: done, [waiting.run]: waiting, [failed.run]: failed };
}

export function threads(now: number, r: Record<string, RunDetail>): Record<string, ThreadDetail> {
  const base = {
    archived: false,
    working: false,
    effort: { effort: "balanced", model: "example/balanced-1", budget: 0.25 },
  };
  return {
    th_demo1: {
      ...base,
      id: "th_demo1",
      title: "Vendor contract renewals",
      created: now - 55 * MIN,
      updated: now - 48 * MIN,
      messages: [
        { role: "user", text: "Summarise the three vendor contracts and list renewal dates.", ts: now - 55 * MIN },
        { role: "run", text: "", ts: now - 54 * MIN, run: "goal_demo0001", goal: r.goal_demo0001.goal },
        {
          role: "assistant",
          text: "Three contracts, three renewals. Northwind (2026-12-15) needs 60 days notice, so that decision is the urgent one. Acme renews 2027-02-01 and Globex 2027-06-30.",
          ts: now - 48 * MIN,
        },
      ],
      runs: { goal_demo0001: r.goal_demo0001 },
    },
    th_demo2: {
      ...base,
      id: "th_demo2",
      title: "Reply to the customer about invoice 1042",
      created: now - 8 * MIN,
      updated: now - 4 * MIN,
      messages: [
        { role: "user", text: "Draft a reply to the customer with the invoice figures.", ts: now - 8 * MIN },
        { role: "run", text: "", ts: now - 7 * MIN, run: "goal_demo0002", goal: r.goal_demo0002.goal, ask: true },
      ],
      runs: { goal_demo0002: r.goal_demo0002 },
    },
    th_demo3: {
      ...base,
      id: "th_demo3",
      title: "Payments status check",
      created: now - 3 * 3600,
      updated: now - 3 * 3600 + 40,
      messages: [
        { role: "user", text: "Check the status page for the payments provider.", ts: now - 3 * 3600 },
        { role: "run", text: "", ts: now - 3 * 3600 + 5, run: "goal_demo0003", goal: r.goal_demo0003.goal, error: true },
      ],
      runs: { goal_demo0003: r.goal_demo0003 },
    },
  };
}

export const approvals = (now: number): ApprovalItem[] => [
  {
    id: "ap_demo_1",
    ts: now - 4 * MIN,
    age_s: 4 * MIN,
    status: "pending",
    who: "orchestrator",
    session: "th_demo2",
    what: "Send invoice 1042 figures to a cloud model to draft the reply.",
    model: "example/balanced-1",
    egress: "api.example-models.test",
    est_cost: 0.004,
    pii: ["email"],
    payload_hash: "demo-hash-1",
    payload_bytes: 412,
    payload: { prompt: "Draft a reply. Invoice 1042: 3 line items, total 1,240.00. Customer: [email redacted]" },
  },
  {
    id: "ap_demo_2",
    ts: now - 20 * MIN,
    age_s: 20 * MIN,
    status: "pending",
    who: "orchestrator",
    session: "th_demo1",
    what: "Use a larger model to double-check the notice periods.",
    model: "example/deep-1",
    egress: "api.example-models.test",
    est_cost: 0.08,
    pii: [],
    payload_hash: "demo-hash-2",
    payload_bytes: 1530,
    payload: { prompt: "Check these notice periods against the contract text." },
  },
  {
    id: "ap_demo_3",
    ts: now - 2 * 3600,
    age_s: 2 * 3600,
    status: "approved",
    who: "orchestrator",
    what: "Fetch the vendor list from the shared drive.",
    model: "example/fast-1",
    egress: "files.example.test",
    est_cost: 0.0008,
    pii: [],
    payload_hash: "demo-hash-3",
    payload_bytes: 96,
    decided_by: "demo@example.com",
    decided_ts: now - 2 * 3600 + 30,
  },
];

export const audit = (now: number): AuditRow[] => [
  { ts: now - 4 * MIN, event: "cloud_call_blocked", result: "blocked", detail: "waiting for approval" },
  { ts: now - 2 * 3600 + 30, event: "human_decision", result: "approved", detail: "ap_demo_3 approved" },
  { ts: now - 2 * 3600, event: "cloud_call_allowed", result: "allowed", detail: "files.example.test" },
  { ts: now - 3 * 3600 + 10, event: "egress_denied", result: "blocked", detail: "status.example.test" },
  { ts: now - 50 * MIN, event: "run_recorded", result: "recorded", detail: "goal_demo0001" },
];

export const devices = (now: number): DevicesPayload => ({
  total: 2,
  online: 1,
  eligible: 1,
  parked_depth: 0,
  devices: [
    { device_id: "demo-laptop", os: "Linux (sample)", capabilities: ["files.read", "shell.exec"], roots: ["~/Documents/demo"], state: "online", last_heartbeat: now - 12, active_grants: 1, inbox_depth: 0 },
    { device_id: "demo-desktop", os: "Linux (sample)", capabilities: ["files.read"], roots: [], state: "asleep", last_heartbeat: now - 5 * 3600, active_grants: 0, inbox_depth: 0 },
  ],
});

export const sandboxes = (): SandboxesPayload => ({
  configured: true,
  up: true,
  api_host: "sandbox.example.test",
  sandboxes: [
    { sandboxID: "sbx-demo-a1", templateID: "code-interpreter" },
    { sandboxID: "sbx-demo-b2", templateID: "code-interpreter" },
  ],
});

export const router = (now: number): RouterPayload => ({
  paid: false,
  dest_host: "api.example-models.test",
  per_route_cost: 0.0004,
  spend: 0.0145,
  cap: 1,
  ledger: [
    { ts: now - 3 * MIN, event: "route", route: "work", engine: "example/balanced-1", cost: 0.004 },
    { ts: now - 49 * MIN, event: "route", route: "work", engine: "example/balanced-1", cost: 0.0042 },
    { ts: now - 50 * MIN, event: "route", route: "plan", engine: "example/fast-1", cost: 0.0004 },
  ],
});

export const skills = (): { skills: Record<string, unknown>[] } => ({
  skills: [
    {
      name: "cross-repo-read",
      description: "Read a second local checkout without copying it into the run's workspace.",
      pulls_tools: ["sandbox"],
      path: "services/skills/catalog/cross-repo-read/SKILL.md",
      status: "ready",
      missing: [],
    },
    {
      name: "web-lookup",
      description: "Answer a fact question from one web lookup instead of guessing.",
      pulls_tools: ["web"],
      path: "services/skills/catalog/web-lookup/SKILL.md",
      status: "ready",
      missing: [],
    },
    {
      name: "recall-notes",
      description: "Look up an earlier decision in long-term memory.",
      pulls_tools: ["memory"],
      path: "services/skills/catalog/recall-notes/SKILL.md",
      status: "degraded",
      missing: ["memory"],
    },
  ],
});

export const health = (): { skills: Record<string, unknown>[] } => ({
  skills: [
    { name: "OpenRouter", kind: "engine", status: "ready", detail: "demo key present" },
    { name: "llama.cpp", kind: "engine", status: "down", detail: ":8080 refused" },
    { name: "Ollama", kind: "engine", status: "not configured", detail: "no base url" },
    { name: "Sandbox", kind: "exec", status: "ready", detail: "cube reachable" },
    { name: "gate", kind: "safety", status: "active", detail: "approvals queued for the owner" },
  ],
});

export const automations = (now: number) => ({
  enabled: true,
  automations: [
    { id: "au_demo1", goal: "Every morning, summarise new vendor emails", every_minutes: 1440, paused: false, next_run: now + 6 * 3600, last_run: now - 18 * 3600, state: "idle" },
    { id: "au_demo2", goal: "Weekly spend report", every_minutes: 10080, paused: true, last_run: now - 5 * 86400, state: "paused" },
  ],
});

export const connectors = (): ConnectorsStatus => ({
  enabled: true,
  toolkits: [
    { slug: "calendar", label: "Calendar", connected: true, account: "demo-calendar" },
    { slug: "mail", label: "Mail", connected: false, account: null },
    { slug: "docs", label: "Documents", connected: false, account: null },
  ],
});

export const jobs = (now: number): AgentJobSummary[] => [
  { job: "job_demo1", goal: "Compare three hosting quotes", started: now - 26 * 3600, status: "finished", budget: 0.5, spent: 0.021, awaiting_approval: false, pending: 0 },
];

export const overview = (now: number, items: ApprovalItem[], rows: AuditRow[]): OverviewPayload => ({
  pending_approvals: items.filter((i) => i.status === "pending"),
  runs: [{ run: "goal_demo0001" }, { run: "goal_demo0002" }, { run: "goal_demo0003" }],
  audit: rows.slice(0, 5) as unknown as Record<string, unknown>[],
  spend: 0.0145,
  cap: 1,
  now,
});
