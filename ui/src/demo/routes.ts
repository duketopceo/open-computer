// Route table for the demo: (method, path) -> typed payload. Anything not listed is "Not part of the demo".
import type { AuditFilter, RouteResult } from "@/lib/api";
import * as fx from "./fixtures";
import { SIMULATED_NOTE, type Store } from "./store";

export class DemoError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

export interface Req {
  method: string;
  path: string;
  query: URLSearchParams;
  body: Record<string, unknown>;
}

type Handler = (r: Req, s: Store, m: string[]) => unknown;
interface Route {
  method: "GET" | "POST";
  re: RegExp;
  run: Handler;
}

const ok = { ok: true, simulated: true, note: SIMULATED_NOTE };
const get = (re: RegExp, run: Handler): Route => ({ method: "GET", re, run });
const post = (re: RegExp, run: Handler): Route => ({ method: "POST", re, run });
const notFound = (what: string) => new DemoError(`${what} no longer exists.`, 404);
const dec = decodeURIComponent;

function routeResult(q: string): RouteResult {
  const heavy = q.trim().split(/\s+/).length > 12;
  return { route: heavy ? "plan" : "work", tier: heavy ? "deep" : "light", engine: heavy ? "example/deep-1" : "example/fast-1", confidence: 0.88, cost: 0, ms: 90 };
}

export const ROUTES: Route[] = [
  get(/^\/api\/session$/, () => fx.session()),
  get(/^\/api\/account$/, () => fx.account()),
  post(/^\/api\/account$/, (r) => ({ ...fx.account(), name: String(r.body.name ?? "Demo visitor") })),
  post(/^\/api\/logout$/, () => ({ ok: true, next: "#demo-signed-out" })),
  get(/^\/api\/effort$/, () => fx.effort()),
  get(/^\/api\/runtime$/, () => fx.runtime()),
  get(/^\/api\/brain$/, () => ({ url: null, health: "unset", hint: "Not used in the demo." })),
  get(/^\/api\/memory$/, () => ({ enabled: false, project: "", counts: { recalls: 0, writes: 0, withheld: 0, forgotten: 0, errors: 0 }, last_error: null, recent_writes: [] })),
  post(/^\/api\/memory\/forget$/, (r) => ({ ok: true, atom: String(r.body.atom ?? "") })),
  get(/^\/api\/voice$/, () => ({ available: false, reason: "Dictation is not part of the demo." })),
  get(/^\/api\/dev$/, () => ({ trace: false, env: false, active: false })),
  post(/^\/api\/dev$/, () => ({ trace: false, env: false, active: false })),

  get(/^\/api\/threads$/, (r, s) => ({ threads: s.listThreads(r.query.get("archived") === "1") })),
  post(/^\/api\/threads$/, (r, s) => ({ thread: s.createThread(String(r.body.text ?? "")) })),
  post(/^\/api\/threads\/clear$/, () => ({ cleared: 0 })),
  get(/^\/api\/threads\/([^/]+)$/, (_r, s, m) => {
    const t = s.threads[dec(m[1])];
    if (!t) throw notFound("This chat");
    return t;
  }),
  post(/^\/api\/threads\/([^/]+)\/messages$/, (r, s, m) => {
    if (!s.sendMessage(dec(m[1]), String(r.body.text ?? ""))) throw notFound("This chat");
    return { ok: true };
  }),
  post(/^\/api\/threads\/([^/]+)\/delete$/, (_r, s, m) => (s.remove("thread", dec(m[1])), ok)),
  post(/^\/api\/threads\/([^/]+)\/archive$/, (r, s, m) => (s.archive("thread", dec(m[1]), !!r.body.archived), ok)),

  get(/^\/api\/runs$/, (r, s) => ({ runs: s.listRuns(r.query.get("archived") === "1", r.query.get("threaded") === "0") })),
  post(/^\/api\/runs\/clear$/, () => ({ cleared: 0 })),
  get(/^\/api\/runs\/([^/]+)$/, (_r, s, m) => {
    const run = s.runs[dec(m[1])];
    if (!run) throw notFound("This run");
    return run;
  }),
  get(/^\/api\/runs\/([^/]+)\/artifacts$/, () => ({ artifacts: [] })),
  get(/^\/api\/runs\/([^/]+)\/events$/, () => {
    throw new DemoError("No event stream: Dev trace is not part of the demo.", 404);
  }),
  post(/^\/api\/runs\/([^/]+)\/delete$/, (_r, s, m) => (s.remove("run", dec(m[1])), ok)),
  post(/^\/api\/runs\/([^/]+)\/archive$/, (r, s, m) => (s.archive("run", dec(m[1]), !!r.body.archived), ok)),
  post(/^\/api\/runs\/([^/]+)\/plan$/, () => ({ state: "approved", simulated: true })),
  post(/^\/goals$/, (r, s) => ({ run: Object.keys(s.runs)[0], effort: String(r.body.effort ?? "balanced"), model: "example/balanced-1", budget: 0.25 })),

  get(/^\/api\/devices$/, (_r, s) => fx.devices(s.now)),
  get(/^\/api\/sandboxes$/, () => fx.sandboxes()),
  get(/^\/api\/router$/, (_r, s) => fx.router(s.now)),
  post(/^\/api\/router\/test$/, (r) => ({ result: routeResult(String(r.body.q ?? "")) })),
  get(/^\/api\/audit$/, (r, s) => {
    const f = (r.query.get("f") ?? "all") as AuditFilter;
    const q = (r.query.get("q") ?? "").toLowerCase();
    const keep = (x: { result: string; event: string; detail: string }) =>
      (f === "all" || (f === "blocked" && x.result === "blocked") || (f === "allowed" && x.result === "allowed") || (f === "decisions" && (x.result === "approved" || x.result === "sent back"))) &&
      (!q || `${x.event} ${x.detail}`.toLowerCase().includes(q));
    const rows = s.audit.filter(keep);
    return { f, q, total: s.audit.length, shown: rows.length, rows };
  }),
  get(/^\/api\/approvals$/, (_r, s) => ({ approvals: s.approvals })),
  post(/^\/api\/approvals\/([^/]+)\/(approve|send-back)$/, (_r, s, m) => {
    if (!s.decideApproval(dec(m[1]), m[2] as "approve" | "send-back")) throw new DemoError("Already decided.", 409);
    return { ok: true, simulated: true };
  }),
  get(/^\/api\/overview$/, (_r, s) => fx.overview(s.now, s.approvals, s.audit)),
  get(/^\/api\/skills$/, () => fx.skills()),
  get(/^\/api\/automations$/, (_r, s) => ({ enabled: true, automations: s.automations })),
  post(/^\/automations$/, (r, s) => {
    s.automations.unshift({ id: s.nextAutomationId(), goal: String(r.body.goal ?? ""), every_minutes: Number(r.body.every_minutes ?? 60), paused: false, state: "idle" });
    return ok;
  }),
  post(/^\/automations\/([^/]+)\/(pause|trigger|delete)$/, (_r, s, m) => {
    const i = s.automations.findIndex((a) => a.id === dec(m[1]));
    if (i < 0) throw notFound("This automation");
    if (m[2] === "delete") s.automations.splice(i, 1);
    else if (m[2] === "pause") s.automations[i].paused = !s.automations[i].paused;
    return ok;
  }),
  get(/^\/api\/agent-jobs$/, (_r, s) => ({ jobs: fx.jobs(s.now) })),
  get(/^\/api\/agent-jobs\/([^/]+)$/, (_r, s, m) => {
    const j = fx.jobs(s.now).find((x) => x.job === dec(m[1]));
    if (!j) throw notFound("This job");
    return { ...j, events: [], approvals: [] };
  }),
  post(/^\/api\/agent-jobs$/, () => ({ job: "job_demo1" })),
  post(/^\/api\/agent-jobs\/([^/]+)\/approvals\/([^/]+)\/(approve|deny)$/, () => ({ ok: true, simulated: true })),

  get(/^\/api\/openrouter$/, (_r, s) => s.openrouter),
  post(/^\/api\/openrouter\/connect$/, (_r, s) => (s.setOpenrouter(true), { url: "#demo-connected" })),
  post(/^\/api\/openrouter\/complete$/, (_r, s) => s.openrouter),
  post(/^\/api\/openrouter\/disconnect$/, (_r, s) => (s.setOpenrouter(false), s.openrouter)),
  get(/^\/api\/connectors$/, (_r, s) => s.connectors),
  post(/^\/api\/connectors\/connect$/, (r, s) => {
    const t = s.connectors.toolkits.find((x) => x.slug === r.body.toolkit);
    if (t) {
      t.connected = true;
      t.account = `demo-${t.slug}`;
    }
    return { url: "#demo-connected" };
  }),
  post(/^\/api\/connectors\/disconnect$/, (r, s) => {
    for (const t of s.connectors.toolkits) if (t.account === r.body.account) (t.connected = false), (t.account = null);
    return { ok: true };
  }),
  post(/^\/api\/transcribe$/, () => {
    throw new DemoError("Dictation is not part of the demo.", 400);
  }),
];

export function dispatch(store: Store, req: Req): unknown {
  for (const r of ROUTES) {
    if (r.method !== req.method) continue;
    const m = r.re.exec(req.path);
    if (m) return r.run(req, store, m);
  }
  throw new DemoError("Not part of the demo.", 404);
}
