// The demo's whole "backend": in-memory state seeded from fixtures. Writes mutate it and nothing else; a reload
// resets it. No network, no storage, no timers beyond the scripted reply below.
import type { ApprovalItem, AuditRow, RunDetail, RunSummary, TaskState, ThreadDetail, ThreadSummary } from "@/lib/api";
import * as fx from "./fixtures";

const STATES: TaskState[] = ["QUEUED", "LEASED", "RUNNING", "DONE", "FAILED", "AWAITING-APPROVAL"];

export const SIMULATED_NOTE = "Demo: nothing was sent.";

export const REPLY =
  "This is the demo, so nothing ran and no model was called. In the real console this request would be planned, " +
  "routed to the cheapest model that fits, and run in a sandbox, with anything that leaves your machine held for your approval.";

export function createStore(now: number = Math.floor(Date.now() / 1000)) {
  const runs = fx.runs(now);
  const threads = fx.threads(now, runs);
  const approvals = fx.approvals(now);
  const audit = fx.audit(now);
  const automations = fx.automations(now).automations as Array<Record<string, unknown> & { id: string; paused: boolean }>;
  const connectors = fx.connectors();
  let openrouter = { connected: false, source: null as "omaseal" | "env" | null };
  let seq = 0;
  const archivedRuns = new Set<string>();
  const timers = new Set<ReturnType<typeof setTimeout>>();

  const summaryOf = (r: RunDetail): RunSummary => {
    const states = Object.fromEntries(STATES.map((s) => [s, 0])) as Record<TaskState, number>;
    for (const s of r.subtasks) states[s.state]++;
    const finished = !r.subtasks.some((s) => s.state === "RUNNING" || s.state === "LEASED" || s.state === "QUEUED" || s.state === "AWAITING-APPROVAL") || r.subtasks.every((s) => s.skipped || s.state === "DONE" || s.state === "FAILED");
    return {
      run: r.run,
      goal: r.goal,
      origin: r.origin,
      device_id: null,
      subtasks: r.subtasks.length,
      states,
      ts: r.started ?? now,
      archived: archivedRuns.has(r.run),
      finished: finished && !states["AWAITING-APPROVAL"],
    };
  };
  const threadSummary = (t: ThreadDetail): ThreadSummary => ({
    id: t.id,
    title: t.title,
    ts: t.updated,
    archived: t.archived,
    working: t.working,
    finished: !t.working && !t.messages.some((m) => m.ask),
  });
  const log = (event: string, result: AuditRow["result"], detail: string) =>
    audit.unshift({ ts: Math.floor(Date.now() / 1000), event, result, detail });

  const reply = (id: string) => {
    const t = setTimeout(() => {
      timers.delete(t);
      const th = threads[id];
      if (!th) return;
      th.messages.push({ role: "assistant", text: REPLY, ts: Math.floor(Date.now() / 1000) });
      th.working = false;
      th.updated = Math.floor(Date.now() / 1000);
    }, 1400);
    timers.add(t);
  };

  return {
    runs,
    threads,
    approvals,
    audit,
    automations,
    connectors,
    now,
    get openrouter() {
      return openrouter;
    },
    setOpenrouter(connected: boolean) {
      openrouter = { connected, source: connected ? "env" : null };
    },
    listRuns: (withArchived: boolean, looseOnly: boolean) =>
      Object.values(runs)
        .filter((r) => !looseOnly || r.origin !== "thread")
        .map(summaryOf)
        .filter((r) => withArchived || !r.archived),
    listThreads: (withArchived: boolean) =>
      Object.values(threads)
        .map(threadSummary)
        .filter((t) => withArchived || !t.archived)
        .sort((a, b) => b.ts - a.ts),
    createThread(text: string): string {
      const id = `th_demo_new${++seq}`;
      const ts = Math.floor(Date.now() / 1000);
      threads[id] = {
        id,
        title: text.slice(0, 48) || "New chat",
        archived: false,
        working: true,
        created: ts,
        updated: ts,
        effort: { effort: "balanced", model: "example/balanced-1", budget: 0.25 },
        messages: [{ role: "user", text, ts }],
        runs: {},
      };
      reply(id);
      return id;
    },
    sendMessage(id: string, text: string) {
      const th = threads[id];
      if (!th) return false;
      const ts = Math.floor(Date.now() / 1000);
      th.messages.push({ role: "user", text, ts });
      th.working = true;
      th.updated = ts;
      reply(id);
      return true;
    },
    decideApproval(id: string, verb: "approve" | "send-back"): ApprovalItem | null {
      const a = approvals.find((x) => x.id === id);
      if (!a || a.status !== "pending") return null;
      a.status = verb === "approve" ? "approved" : "sent_back";
      a.decided_by = "demo@example.com";
      a.decided_ts = Math.floor(Date.now() / 1000);
      log("human_decision", verb === "approve" ? "approved" : "sent back", `${id} ${verb === "approve" ? "approved" : "sent back"}`);
      for (const r of Object.values(runs))
        for (const s of r.subtasks)
          if (s.approval === id) {
            s.state = verb === "approve" ? "DONE" : "FAILED";
            if (verb === "approve") s.result = "Approved in the demo; no model was called.";
            else s.error = "Sent back by the reviewer.";
            s.finished_ts = a.decided_ts;
          }
      return a;
    },
    archive(kind: "thread" | "run", id: string, archived: boolean) {
      if (kind === "thread" && threads[id]) threads[id].archived = archived;
      else if (kind === "run") archived ? archivedRuns.add(id) : archivedRuns.delete(id);
    },
    remove(kind: "thread" | "run", id: string) {
      if (kind === "thread") delete threads[id];
      else delete runs[id];
    },
    dispose() {
      timers.forEach(clearTimeout);
      timers.clear();
    },
  };
}

export type Store = ReturnType<typeof createStore>;
