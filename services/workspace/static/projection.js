// Projection: the whole workspace state is a pure fold over the goal's events. Same events in, same state out.
// Nothing here reads the clock, the DOM or storage, so a replay is reproducible by construction.

export function createState(goal) {
  return {
    goal, lastSeq: 0, firstTs: null, lastTs: 0, count: 0,
    run: { status: "queued", reason: null, title: "", goalText: "", summary: "", model: "", capUsd: 0, box: null, startedTs: null, endedTs: null },
    steps: new Map(), tools: new Map(), sources: new Map(), artifacts: new Map(), changes: new Map(),
    approvals: new Map(), notes: new Map(), errors: new Map(),
    cost: { spent: 0, cap: 0, tokens: { in: 0, out: 0, cached: 0 }, byModel: [], context: { used: 0, window: 0 }, threshold: 0 },
    timeline: [], tlKeys: new Set(), cites: new Map(), // n -> {count, firstNote}
    order: 0, runEvents: new Map(), lastRunSeq: 0,
  };
}

function seen(s, key, kind, ref, seq) {
  if (s.tlKeys.has(key)) return;
  s.tlKeys.add(key);
  s.timeline.push({ key, kind, ref, seq });
}

function upsert(map, id, data, s) {
  const prev = map.get(id);
  const next = Object.assign({}, prev || { order: s.order++ }, data);
  map.set(id, next);
  return { prev, next };
}

export function apply(s, ev) {
  if (ev.seq <= s.lastSeq) return false; // duplicate or replayed after resume: ignore
  s.lastSeq = ev.seq;
  s.lastTs = ev.ts;
  if (s.firstTs == null) s.firstTs = ev.ts;
  s.count++;
  const d = ev.data;
  switch (ev.type) {
    case "run": {
      const r = s.run, was = r.status;
      r.status = d.status;
      r.reason = (d.status === "waiting" || d.status === "paused") ? (d.reason || null) : null;
      for (const k of ["title", "summary", "model", "box"]) if (d[k] !== undefined) r[k] = d[k];
      if (d.goal_text !== undefined) r.goalText = d.goal_text;
      if (d.cap_usd !== undefined) { r.capUsd = d.cap_usd; s.cost.cap = d.cap_usd; }
      if (r.startedTs == null) r.startedTs = ev.ts;
      if (["done", "failed", "cancelled"].includes(d.status)) r.endedTs = ev.ts; else r.endedTs = null;
      if (was !== d.status && (["paused", "done", "failed", "cancelled"].includes(d.status) || (was === "paused" && d.status === "running")))
        seen(s, "run:" + ev.seq, "run", String(ev.seq), ev.seq);
      s.lastRunSeq = ev.seq;
      s.runEvents.set(String(ev.seq), { status: d.status, reason: d.reason || null, summary: d.summary || "", ts: ev.ts, capUsd: d.cap_usd });
      break;
    }
    case "step": {
      const { prev, next } = upsert(s.steps, d.id, d, s);
      if (d.status === "running" && next.startedTs == null) next.startedTs = ev.ts;
      if (["done", "failed", "skipped"].includes(d.status)) { if (next.startedTs == null) next.startedTs = ev.ts; next.endedTs = ev.ts; }
      if (d.status !== "pending") seen(s, "step:" + d.id, "step", d.id, ev.seq);
      void prev;
      break;
    }
    case "tool_call": {
      const { next } = upsert(s.tools, d.id, d, s);
      if (next.firstTs == null) next.firstTs = ev.ts;
      next.lastTs = ev.ts;
      next.step = next.step || "step_none";
      if (!s.steps.has(next.step)) { s.steps.set(next.step, { id: next.step, title: "Other activity", status: "running", kind: "step", order: s.order++, synthetic: true }); seen(s, "step:" + next.step, "step", next.step, ev.seq); }
      break;
    }
    case "source": upsert(s.sources, d.id, d, s); break;
    case "artifact": {
      const { prev, next } = upsert(s.artifacts, d.id, d, s);
      next.ts = ev.ts;
      if (!prev || prev.version !== next.version) next.unreviewed = true;
      seen(s, "artifact:" + d.id, "artifact", d.id, ev.seq);
      break;
    }
    case "change": upsert(s.changes, d.id, d, s); break;
    case "approval": {
      const { prev, next } = upsert(s.approvals, d.id, d, s);
      if (!prev) { next.askedTs = ev.ts; next.actor = ev.actor || null; }
      if (d.status !== "pending") next.decidedTs = d.decided_at || ev.ts;
      seen(s, "approval:" + d.id, "approval", d.id, ev.seq);
      break;
    }
    case "cost": {
      const c = s.cost;
      c.spent = d.spent_usd; c.cap = d.cap_usd;
      if (d.tokens) c.tokens = d.tokens;
      if (d.by_model) c.byModel = d.by_model;
      if (d.context) c.context = d.context;
      if (d.threshold) c.threshold = d.threshold;
      break;
    }
    case "note": {
      const { prev, next } = upsert(s.notes, d.id, d, s);
      next.ts = prev ? prev.ts : ev.ts;
      next.actor = ev.actor || null;
      // Citation bookkeeping: count [n] markers, remember the first note that cites each source.
      if (d.role === "agent") {
        const re = /\[(\d+)\]/g; let m;
        while ((m = re.exec(d.text || ""))) {
          const n = Number(m[1]);
          const c = s.cites.get(n) || { count: 0, firstNote: d.id, notes: new Set() };
          if (!c.notes.has(d.id + ":" + m.index)) { c.notes.add(d.id + ":" + m.index); c.count++; }
          s.cites.set(n, c);
        }
      }
      seen(s, "note:" + d.id, "note", d.id, ev.seq);
      break;
    }
    case "error": upsert(s.errors, d.id, d, s); seen(s, "error:" + d.id, "error", d.id, ev.seq); break;
  }
  return true;
}

// ---- selectors (pure) -----------------------------------------------------------------------------
export const list = (map) => Array.from(map.values()).sort((a, b) => a.order - b.order);
export const pendingApprovals = (s) => list(s.approvals).filter((a) => a.status === "pending");
export const sourceByN = (s, n) => list(s.sources).find((x) => x.n === n) || null;
export const stepTools = (s, id) => list(s.tools).filter((t) => t.step === id);
export const spentPct = (s) => (s.cost.cap > 0 ? s.cost.spent / s.cost.cap : 0);
export const elapsed = (s) => (s.run.startedTs == null ? 0 : (s.run.endedTs != null ? s.run.endedTs : s.lastTs) - s.run.startedTs);
export const contextPct = (s) => (s.cost.context.window ? s.cost.context.used / s.cost.context.window : 0);
export const isPausedForCap = (s) => s.run.status === "paused" && s.run.reason === "cap";
export const groupOf = (s) => {
  const st = s.run.status;
  if (st === "failed" || st === "cancelled") return "failed";
  if (pendingApprovals(s).length || st === "waiting" || st === "paused") return "needs_you";
  return st === "done" ? "done" : "running";
};

// Counts shown on tabs, for the determinism cross-check against the server's own fold.
export function summary(s) {
  return {
    status: s.run.status, reason: s.run.reason, spent_usd: s.cost.spent, cap_usd: s.cost.cap, pending_approvals: pendingApprovals(s).length,
    artifacts: s.artifacts.size, sources: s.sources.size, changes: s.changes.size, last_seq: s.lastSeq, group: groupOf(s),
  };
}
