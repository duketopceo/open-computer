import { useCallback, useEffect, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { m } from "motion/react";
import { ApiError, errorMessage } from "@/lib/api";
import { useDevTrace } from "@/lib/devmode";
import { cost, dur } from "@/lib/format";
import { SPRING } from "@/lib/motion";
import { MotionRoot, useMotionAllowed } from "@/lib/MotionRoot";
import {
  EVENTS_PAGE,
  applyEvents,
  emptyTimeline,
  exportUrl,
  getRunEvents,
  runTerminal,
  stepDisplay,
  type Step,
  type Timeline as TimelineState,
} from "@/lib/runEvents";
import { Chip, Dot, StateWord } from "../primitives";
import { CallRow } from "./CallRow";
import { ExecRow } from "./ExecRow";
import { ErrorBlock, StepCard } from "./StepCard";

/** Rows at or below this render as a plain list (axe and keyboard match the run view). */
export const VIRTUALIZE_ABOVE = 60;
const POLL_MS = 1000;

type Phase = "loading" | "live" | "done" | "off" | "error";

/** One poll loop per run: 1 s while live, only events after the cursor, stops when finished. */
function useRunEvents(runId: string) {
  const [tl, setTl] = useState<TimelineState>(emptyTimeline);
  const [phase, setPhase] = useState<Phase>("loading");
  const [problem, setProblem] = useState("");
  const [baseline, setBaseline] = useState<number | null>(null);
  useEffect(() => {
    let dead = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let after = 0;
    setTl(emptyTimeline());
    setPhase("loading");
    setProblem("");
    setBaseline(null);
    const tick = async () => {
      try {
        const r = await getRunEvents(runId, after);
        if (dead) return;
        after = r.events.length ? r.events[r.events.length - 1].seq : after;
        setTl((p) => applyEvents(p, r.events));
        setBaseline((b) => b ?? after);
        setProblem("");
        if (r.events.length >= EVENTS_PAGE) timer = setTimeout(tick, 0); // catch up on a long stream
        else if (r.finished) setPhase("done");
        else {
          setPhase("live");
          timer = setTimeout(tick, POLL_MS);
        }
      } catch (e) {
        if (dead) return;
        if (e instanceof ApiError && e.status === 404) return setPhase("off");
        if (e instanceof ApiError && e.kind === "auth") {
          setPhase("error");
          return setProblem(e.message);
        }
        setProblem(errorMessage(e, "Could not load the timeline."));
        timer = setTimeout(tick, POLL_MS);
      }
    };
    tick();
    return () => {
      dead = true;
      clearTimeout(timer);
    };
  }, [runId]);
  return { tl, phase, problem, baseline };
}

/** The one shared clock: re-renders the view once a second while something is live. */
function useNow(live: boolean) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!live) return;
    setNow(Date.now() / 1000);
    const t = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, [live]);
  return now;
}

const STATUS_STATE = {
  queued: "QUEUED",
  planning: "RUNNING",
  running: "RUNNING",
  waiting: "AWAITING-APPROVAL",
  paused: "AWAITING-APPROVAL",
  done: "DONE",
  failed: "FAILED",
  cancelled: "FAILED",
} as const;

/** The sandbox as the owner sees it: the latest command that ran (or is running) and its output tail. */
function Activity({ tl }: { tl: TimelineState }) {
  const execs = tl.steps.flatMap((s) => (tl.execsByStep[s.id] ?? []).map((id) => ({ step: s, x: tl.execs[id] })));
  const last = execs[execs.length - 1];
  if (!last) return null;
  const { step, x } = last;
  const running = x.status === "running";
  return (
    <section aria-label="Sandbox activity" className="mb-4 rounded-xl border border-line bg-surface-1" data-testid="activity">
      <p className="flex items-center gap-2 border-b border-line px-4 py-2 text-xs">
        {running && <span aria-hidden className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-accent" />}
        <span className="mono uppercase tracking-wider text-muted">{running ? "Sandbox · running" : "Sandbox · last command"}</span>
        <span className="min-w-0 flex-1 truncate text-ink-muted">{step.title}</span>
      </p>
      <ExecRow exec={x} />
    </section>
  );
}

export function Timeline({ runId, goal, embedded = false }: { runId: string; goal?: string; embedded?: boolean }) {
  const { tl, phase, problem, baseline } = useRunEvents(runId);
  const devOn = useDevTrace().active;
  const live = phase === "live" || phase === "loading";
  const now = useNow(live);
  const [openIds, setOpenIds] = useState<Set<string>>(() => new Set());
  const toggle = useCallback(
    (id: string) =>
      setOpenIds((s) => {
        const n = new Set(s);
        if (!n.delete(id)) n.add(id);
        return n;
      }),
    [],
  );

  // Polite announcements of state changes, never on the first load.
  const seen = useRef<Map<string, string> | null>(null);
  const [said, setSaid] = useState("");
  useEffect(() => {
    const cur = new Map(tl.steps.map((s) => [s.id, stepDisplay(s)]));
    const prev = seen.current;
    seen.current = cur;
    if (!prev || baseline == null) return;
    const changed = tl.steps.filter((s) => prev.get(s.id) !== cur.get(s.id));
    if (changed.length) setSaid(changed.slice(-3).map((s) => `${s.title}: ${cur.get(s.id)}`).join(". "));
  }, [tl.steps, baseline]);

  if (phase === "off") {
    return (
      <div className="rounded-2xl border border-line bg-surface-1 p-6 text-sm" role="status">
        <p className="font-medium text-ink">No timeline for this run</p>
        <p className="mt-1 text-muted">
          Dev trace was off when this run was submitted, so nothing was recorded. Turn on Dev trace under Operator in the sidebar, then
          run the goal again.
        </p>
      </div>
    );
  }
  if (phase === "loading" && !problem) {
    return (
      <p className="text-sm text-muted" role="status">
        Loading timeline…
      </p>
    );
  }

  const runLevelCalls = (tl.callsByStep[""] ?? []).map((id) => tl.calls[id]);
  const runErrors = (tl.errorsByStep[""] ?? []).map((id) => tl.errors[id]);
  const stepCost = Object.values(tl.calls).reduce((a, c) => a + (c.cost_usd ?? 0), 0);
  const spent = tl.cost?.spent_usd ?? stepCost;
  const cap = tl.cost?.cap_usd;
  const run = tl.run;
  const end = run?.endTs ?? (runTerminal(tl) ? undefined : now);
  const state = run ? STATUS_STATE[run.status] : "QUEUED";
  const rows = tl.steps.map((s) => ({
    step: s,
    live: stepDisplay(s) === "running" || stepDisplay(s) === "waiting",
  }));

  const renderRow = (s: Step, isLive: boolean, slide: boolean) => (
    <StepCard
      step={s}
      route={tl.routes[s.id]}
      calls={(tl.callsByStep[s.id] ?? []).map((id) => tl.calls[id])}
      execs={(tl.execsByStep[s.id] ?? []).map((id) => tl.execs[id])}
      errors={(tl.errorsByStep[s.id] ?? []).map((id) => tl.errors[id])}
      open={openIds.has(s.id)}
      onToggle={toggle}
      now={isLive ? now : null}
      slide={slide}
      raw={devOn}
    />
  );

  return (
    <div>
      <header className={embedded ? "mb-4" : "mb-5"}>
        {!embedded && <h1 className="font-semibold text-xl leading-snug text-ink">{goal || run?.title || "Run timeline"}</h1>}
        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
          {run && live && <Dot state="RUNNING" />}
          <StateWord state={state} />
          {run?.reason && <span className="mono text-muted">{run.reason}</span>}
          <span className="mono text-muted">
            {run ? dur(run.startTs, end) : ""} · {cost(spent)}
            {cap != null && ` of ${cost(cap)}`}
          </span>
          {!embedded && <Chip>{devOn ? "Dev trace on" : "Dev trace off"}</Chip>}
          {!embedded && (
            <a
              href={exportUrl(runId)}
              download
              className="inline-flex h-6 items-center rounded-full border border-line px-2.5 font-medium text-ink hover:bg-surface-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              Export JSONL
            </a>
          )}
        </div>
        {problem && (
          <p className="mt-3 text-xs text-attention" role="status">
            {problem} Retrying…
          </p>
        )}
        {runErrors.map((e) => (
          <div key={e.id} className="mt-3">
            <ErrorBlock e={e} />
          </div>
        ))}
        {runLevelCalls.length > 0 && (
          <details className="mt-3">
            <summary className="cursor-pointer select-none text-xs text-muted hover:text-ink">
              Planning and routing ({runLevelCalls.length} model {runLevelCalls.length === 1 ? "call" : "calls"})
            </summary>
            <div className="mt-2 space-y-2">
              {runLevelCalls.map((c) => (
                <CallRow key={c.id} call={c} raw={devOn} />
              ))}
            </div>
          </details>
        )}
      </header>

      <p className="sr-only" role="status" aria-live="polite">
        {said}
      </p>

      {embedded && <Activity tl={tl} />}

      {rows.length === 0 ? (
        <p className="text-sm text-muted">No steps yet. The first one appears here as soon as the plan is made.</p>
      ) : rows.length > VIRTUALIZE_ABOVE ? (
        <MotionRoot>
          <VirtualRows rows={rows} render={(s, l) => renderRow(s, l, false)} />
        </MotionRoot>
      ) : (
        <MotionRoot>
          <PlainRows rows={rows} render={(st, l) => renderRow(st, l, baseline != null && st.firstSeq > baseline)} />
        </MotionRoot>
      )}
    </div>
  );
}

/** The plain list: when a card opens or a new one arrives, the cards below glide to their new place.
 *  Virtualized lists skip layout animation (rows are positioned by the virtualizer). */
function PlainRows({ rows, render }: { rows: { step: Step; live: boolean }[]; render: (s: Step, live: boolean) => React.ReactNode }) {
  const moving = useMotionAllowed();
  return (
    <ul className="space-y-2" aria-label="Steps">
      {rows.map(({ step, live }) => (
        <m.li key={step.id} layout={moving ? "position" : false} transition={SPRING.reflow}>
          {render(step, live)}
        </m.li>
      ))}
    </ul>
  );
}

function VirtualRows({ rows, render }: { rows: { step: Step; live: boolean }[]; render: (s: Step, live: boolean) => React.ReactNode }) {
  const parent = useRef<HTMLDivElement>(null);
  const v = useVirtualizer({
    count: rows.length,
    getScrollElement: () => parent.current,
    estimateSize: () => 76,
    overscan: 8,
    getItemKey: (i) => rows[i].step.id,
  });
  const items = v.getVirtualItems();
  return (
    <div ref={parent} role="region" aria-label="Steps" tabIndex={0} className="h-[65vh] min-h-72 overflow-y-auto">
      <ul aria-label="Steps" className="relative w-full" style={{ height: v.getTotalSize() }}>
        {items.map((it) => (
          <li
            key={it.key}
            data-index={it.index}
            ref={v.measureElement}
            aria-posinset={it.index + 1}
            aria-setsize={rows.length}
            className="absolute left-0 top-0 w-full pb-2"
            style={{ transform: `translateY(${it.start}px)` }}
          >
            {render(rows[it.index].step, rows[it.index].live)}
          </li>
        ))}
      </ul>
    </div>
  );
}

