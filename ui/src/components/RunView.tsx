import { useEffect, useState } from "react";
import type { RunDetail, SubTask } from "@/lib/api";
import { cost, dur, totalCost } from "@/lib/format";
import { engine, errorKind, fallbackWhy, modelName, placement, skippedWhy, waitingText } from "@/lib/friendly";
import { hasStream } from "@/lib/runEvents";
import { Artifacts } from "./Artifacts";
import { PlanCard } from "./PlanCard";
import { Timeline } from "./runtime/Timeline";
import { Chip, Dot, StateWord } from "./primitives";

/** `compact` is the run as it appears inside a chat: the goal is already in the thread above it. */
export function RunView({ run, compact = false, onChanged }: { run: RunDetail; compact?: boolean; onChanged?: () => void }) {
  const [decided, setDecided] = useState(false);
  const states = count(run);
  const active = run.subtasks.some((s) => s.state === "RUNNING" || s.state === "LEASED");
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [active]);
  // The Timeline link appears only when this run has an event stream (dev trace was on at submit).
  const [stream, setStream] = useState(false);
  // With a stream the run page opens on the live view; "Steps" is the plain list of what each step returned.
  const [view, setView] = useState<"live" | "steps">("live");
  const showLive = stream && !compact && view === "live";
  useEffect(() => {
    let dead = false;
    setStream(false);
    hasStream(run.run).then((ok) => !dead && setStream(ok));
    return () => {
      dead = true;
    };
  }, [run.run]);
  const planned = run.plan?.state === "planned";
  return (
    <div className={compact ? "w-full animate-fade-up" : "mx-auto w-full max-w-3xl animate-fade-up"}>
      <header className={compact ? "mb-3" : "mb-6"}>
        {!compact && <h1 className="font-display text-2xl leading-snug text-ink">{run.goal}</h1>}
        <div className={compact ? "flex flex-wrap items-center gap-2 text-xs" : "mt-3 flex flex-wrap items-center gap-2 text-xs"}>
          <StateWord state={planned ? "AWAITING-APPROVAL" : aggregate(states, active)} />
          <span className="mono text-muted">
            {states.DONE}/{run.declared} done · {totalCost(run.subtasks)} ·{" "}
            {run.started != null ? dur(run.started, run.ended ?? Date.now() / 1000) : ""}
          </span>
          {stream && (
            <a
              href={`#${run.run}/timeline`}
              className="inline-flex h-6 items-center rounded-full border border-line px-2.5 font-medium text-ink hover:bg-surface-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              Timeline
            </a>
          )}
          <span className="mono text-muted">origin {run.origin}</span>
          {run.device_id && <span className="mono text-muted">device {run.device_id}</span>}
          {run.effort && (
            <span className="mono text-muted">
              effort {run.effort.id} · {run.effort.model} · {cost(run.effort.spent)} of {cost(run.effort.budget)}
            </span>
          )}
        </div>
        {run.effort?.paused && (
          <p className="mt-3 rounded-lg border border-attention bg-attention-soft px-3 py-2 text-sm text-attention" role="status">
            <span className="mono uppercase tracking-wider">Paused · budget</span> This run reached its{" "}
            {cost(run.effort.budget)} budget. Nothing was cancelled; the remaining steps stay queued.
          </p>
        )}
        {run.plan_thinking && <Thinking text={run.plan_thinking} className="mt-3" />}
      </header>

      {planned && !decided && (
        <PlanCard
          runId={run.run}
          plan={run.plan!}
          onDecided={() => {
            setDecided(true);
            onChanged?.();
          }}
        />
      )}
      {planned && decided && (
        <p className="mb-4 text-sm text-muted" role="status">
          Decision saved. Updating the run…
        </p>
      )}
      {run.plan?.state === "cancelled" && (
        <p className="mb-4 rounded-lg border border-line bg-surface-1 px-3 py-2 text-sm text-ink-muted" role="status">
          You cancelled this plan. Nothing ran and nothing was spent beyond planning.
        </p>
      )}
      {stream && !compact && (
        <div role="tablist" aria-label="Run view" className="mb-3 inline-flex rounded-lg border border-line bg-surface-1 p-0.5 text-xs">
          {(["live", "steps"] as const).map((v) => (
            <button
              key={v}
              role="tab"
              aria-selected={view === v}
              onClick={() => setView(v)}
              className={`rounded-md px-3 py-1 font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${view === v ? "bg-surface-3 text-ink" : "text-muted hover:text-ink"}`}
            >
              {v === "live" ? "Live" : "Steps"}
            </button>
          ))}
        </div>
      )}
      {showLive ? (
        <Timeline key={run.run} runId={run.run} goal={run.goal} embedded />
      ) : (
      run.subtasks.length > 0 && (
        <ol className="divide-y divide-line rounded-2xl border border-line bg-surface-1 shadow-card">
          {run.subtasks.map((s) => (
            <SubTaskRow key={s.subtask} s={s} steps={run.subtasks} />
          ))}
        </ol>
      ))}

      {!compact && <Artifacts runId={run.run} refresh={states.DONE} />}

      {run.error && (
        <div className="mt-4 rounded-2xl border border-urgent bg-urgent-soft p-4 text-sm text-urgent">
          <p className="font-medium">{run.error_kind ?? "failed"}</p>
          <p>{run.error}</p>
          {run.hint && <p className="mt-1 text-urgent">{run.hint}</p>}
        </div>
      )}
    </div>
  );
}

function SubTaskRow({ s, steps }: { s: SubTask; steps: SubTask[] }) {
  if (s.skipped) return <SkippedRow s={s} why={skippedWhy(s.error, steps)} />;
  const live = s.state === "RUNNING" || s.state === "LEASED";
  const took = live ? dur(s.started_ts, Date.now() / 1000) : dur(s.started_ts, s.finished_ts);
  return (
    <li className="flex items-start gap-3 px-4 py-3">
      <span className="mt-1.5">
        <Dot state={s.state} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-3">
          <p className="truncate text-sm font-medium text-ink">{s.title || s.subtask}</p>
          <div className="flex shrink-0 items-center gap-2">
            {engine(s.engine) && <span className="mono text-muted">{engine(s.engine)}</span>}
            {took && (
              <span className="mono text-muted" title={live ? "Running for" : "Run time"}>
                {took}
              </span>
            )}
            <span className="mono text-muted">{cost(s.cost)}</span>
            <StateWord state={s.state} />
          </div>
        </div>
        <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-muted">
          <span className="mono">{s.route || "route —"}</span>
          <span>·</span>
          <span className="mono">{placement(s.placement)}</span>
          {(s.pii?.length ?? 0) > 0 && (
            <span className="text-accent" title={`Detected: ${(s.pii ?? []).join(", ")}`}>
              · private details
            </span>
          )}
        </p>
        {s.waiting_on && s.state === "QUEUED" && (
          <p className="mt-1 text-xs text-muted" title={s.waiting_on.detail}>
            <span aria-hidden="true">– </span>
            {waitingText(s.waiting_on.kind, s.waiting_on.attempt, s.waiting_on.max)}
            {dur(s.waiting_on.since, Date.now() / 1000) && (
              <span className="mono"> · {dur(s.waiting_on.since, Date.now() / 1000)}</span>
            )}
          </p>
        )}
        <StepTruth s={s} />
        {s.state === "DONE" && s.result && <StepResult text={s.result} />}
        {s.state === "AWAITING-APPROVAL" && (
          <p className="mt-1 text-xs text-accent">
            {s.reason}.{" "}
            <a href="/approvals" className="font-medium underline underline-offset-2">
              Review the approval
            </a>
          </p>
        )}
        {s.state === "FAILED" && (
          <p className="mt-1 text-xs text-urgent">
            {errorKind(s.error_kind)}: {s.error}
            {s.hint ? ` — ${s.hint}` : ""}
          </p>
        )}
        {s.thinking && <Thinking text={s.thinking} className="mt-2" />}
      </div>
    </li>
  );
}

/** Which model served the step and who routed it, as two chips; plus a plain note when no reasoning came back. */
function StepTruth({ s }: { s: SubTask }) {
  const why = fallbackWhy(s.router);
  const noReasoning = s.state === "DONE" && s.worker?.tokens_reasoning === 0 && !s.thinking;
  if (!s.worker && !s.router && !noReasoning) return null;
  return (
    <p className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
      {s.worker && (
        <Chip filled title={`Model ${s.worker.model}${s.worker.provider ? ` via ${s.worker.provider}` : ""}`}>
          <span className="mono">{modelName(s.worker.model)}</span>
        </Chip>
      )}
      {s.router && engine(s.router.engine) && (
        <Chip title={why ? `Routed by ${engine(s.router.engine)} — ${why}` : undefined}>
          routed by {engine(s.router.engine)}
          {why && <span className="sr-only"> — {why}</span>}
        </Chip>
      )}
      {noReasoning && <span>no reasoning returned</span>}
    </p>
  );
}

/** A step that never ran because a dependency failed: muted, no duration, no cost, no accent. */
function SkippedRow({ s, why }: { s: SubTask; why: string }) {
  return (
    <li className="flex items-start gap-3 px-4 py-3">
      <span className="mt-1.5">
        <span aria-hidden="true" className="inline-block h-2 w-2 rounded-sm border border-dashed border-muted" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-3">
          <p className="truncate text-sm font-medium text-muted">{s.title || s.subtask}</p>
          <span className="mono shrink-0 text-muted">{why}</span>
        </div>
      </div>
    </li>
  );
}

const MARKUP_RE = /<\/?(html|head|body|div|span|svg|script|style|table|section|article|template)\b/i;
const JSONISH_RE = /^\s*[[{]/;

function isHeavy(text: string): boolean {
  const lines = text.split("\n");
  return lines.length > 8 || MARKUP_RE.test(text) || JSONISH_RE.test(text);
}

function StepResult({ text }: { text: string }) {
  if (!isHeavy(text)) {
    return (
      <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-ink">{text}</p>
    );
  }
  const lines = text.split("\n").length;
  return (
    <details className="mt-2">
      <summary className="cursor-pointer select-none text-xs text-muted hover:text-ink">
        Output — {lines} lines
      </summary>
      <pre className="mt-1 max-h-72 overflow-auto rounded-lg bg-canvas p-3 text-xs leading-relaxed text-ink">
        {text}
      </pre>
    </details>
  );
}

function Thinking({ text, className }: { text: string; className?: string }) {
  return (
    <details className={className}>
      <summary className="cursor-pointer select-none text-xs text-muted hover:text-ink">Thinking</summary>
      <pre className="mt-1 max-h-64 overflow-y-auto whitespace-pre-wrap rounded-lg bg-canvas p-3 text-xs leading-relaxed text-muted">
        {text}
      </pre>
    </details>
  );
}

function count(run: RunDetail) {
  return run.subtasks.reduce(
    (a, s) => {
      a[s.state]++;
      return a;
    },
    { QUEUED: 0, LEASED: 0, RUNNING: 0, DONE: 0, FAILED: 0, "AWAITING-APPROVAL": 0 } as Record<
      string,
      number
    >,
  );
}

function aggregate(states: Record<string, number>, active: boolean) {
  if (states.FAILED && !active) return "FAILED" as const;
  if (active) return "RUNNING" as const;
  if (states["AWAITING-APPROVAL"]) return "AWAITING-APPROVAL" as const;
  if (states.DONE && states.DONE === states.DONE + states.QUEUED + states.LEASED + states.RUNNING)
    return "DONE" as const;
  if (states.QUEUED || states.LEASED) return "RUNNING" as const;
  return "QUEUED" as const;
}