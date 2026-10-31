import { useEffect, useState } from "react";
import type { RunDetail, SubTask } from "@/lib/api";
import { cost, dur, totalCost } from "@/lib/format";
import { engine, errorKind, placement } from "@/lib/friendly";
import { Dot, StateWord } from "./primitives";

export function RunView({ run }: { run: RunDetail }) {
  const states = count(run);
  const active = run.subtasks.some((s) => s.state === "RUNNING" || s.state === "LEASED");
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [active]);
  return (
    <div className="mx-auto w-full max-w-3xl animate-fade-up">
      <header className="mb-6">
        <h1 className="font-display text-2xl leading-snug text-ink">{run.goal}</h1>
        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
          <StateWord state={aggregate(states, active)} />
          <span className="mono text-muted">
            {states.done}/{run.declared} done · {totalCost(run.subtasks)} ·{" "}
            {run.started != null ? dur(run.started, run.ended ?? Date.now() / 1000) : ""}
          </span>
          <span className="mono text-muted">origin {run.origin}</span>
          {run.device_id && <span className="mono text-muted">device {run.device_id}</span>}
          {run.effort && (
            <span className="mono text-muted">
              effort {run.effort.id} · {run.effort.model} · {cost(run.effort.spent)} of {cost(run.effort.budget)}
            </span>
          )}
        </div>
        {run.effort?.paused && (
          <p className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900" role="status">
            <span className="mono uppercase tracking-wider">Paused · budget</span> This run reached its{" "}
            {cost(run.effort.budget)} budget. Nothing was cancelled; the remaining steps stay queued.
          </p>
        )}
        {run.plan_thinking && <Thinking text={run.plan_thinking} className="mt-3" />}
      </header>

      <ol className="divide-y divide-line rounded-2xl border border-line bg-white shadow-card">
        {run.subtasks.map((s) => (
          <SubTaskRow key={s.subtask} s={s} />
        ))}
      </ol>

      {run.error && (
        <div className="mt-4 rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          <p className="font-medium">{run.error_kind ?? "failed"}</p>
          <p>{run.error}</p>
          {run.hint && <p className="mt-1 text-red-600/80">{run.hint}</p>}
        </div>
      )}
    </div>
  );
}

function SubTaskRow({ s }: { s: SubTask }) {
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
          {s.pii && <span className="text-violet-600">· private</span>}
        </p>
        {s.state === "DONE" && s.result && <StepResult text={s.result} />}
        {s.state === "FAILED" && (
          <p className="mt-1 text-xs text-red-600">
            {errorKind(s.error_kind)}: {s.error}
            {s.hint ? ` — ${s.hint}` : ""}
          </p>
        )}
        {s.thinking && <Thinking text={s.thinking} className="mt-2" />}
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
      <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-ink/90">{text}</p>
    );
  }
  const lines = text.split("\n").length;
  return (
    <details className="mt-2">
      <summary className="cursor-pointer select-none text-xs text-muted hover:text-ink">
        Output — {lines} lines
      </summary>
      <pre className="mt-1 max-h-72 overflow-auto rounded-lg bg-canvas p-3 text-xs leading-relaxed text-ink/90">
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