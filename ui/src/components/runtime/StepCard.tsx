import { memo } from "react";
import { cn } from "@/lib/cn";
import { cost, dur } from "@/lib/format";
import { plainError, waitingLabel, words } from "@/lib/friendly";
import { stepDisplay, type ErrorData, type LlmCallData, type RouteData, type Step, type ToolCallData } from "@/lib/runEvents";
import { Chip } from "../primitives";
import { CallRow } from "./CallRow";
import { ExecRow } from "./ExecRow";
import { StepMark } from "./StepMark";

export interface StepCardProps {
  step: Step;
  route?: RouteData;
  calls: LlmCallData[];
  execs: ToolCallData[];
  errors: ErrorData[];
  open: boolean;
  onToggle: (id: string) => void;
  /** shared 1 s clock (epoch seconds); only passed to rows that are live */
  now: number | null;
  /** slide in (a row that arrived after the first load) */
  slide: boolean;
  /** dev mode: show raw model detail (plan JSON, reasoning) inside the step */
  raw?: boolean;
}

export const ErrorBlock = ({ e }: { e: ErrorData }) => (
  <div className="rounded-lg border border-urgent bg-urgent-soft px-3 py-2 text-xs text-urgent">
    <p className="font-medium">{plainError(e.code)}</p>
    {e.hint && <p className="mt-0.5">{e.hint}</p>}
    <details className="mt-1">
      <summary className="cursor-pointer select-none">Details</summary>
      <pre tabIndex={0} aria-label="Error details" className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded-lg bg-surface-1 p-3 text-ink-muted">
        {e.message}
      </pre>
    </details>
  </div>
);

function StepCardView({ step, route, calls, execs, errors, open, onToggle, now, slide, raw = false }: StepCardProps) {
  const display = stepDisplay(step);
  const live = display === "running" || display === "waiting";
  const end = step.endedTs ?? (live ? now ?? undefined : undefined);
  const elapsed = display === "skipped" || step.startedTs == null ? "" : dur(step.startedTs, end);
  const waiting = step.waiting_on;
  const waitFor = waiting?.since != null && now != null ? dur(waiting.since, now) : "";
  const served = step.model || [...calls].reverse().find((c) => c.kind === "work" && c.model_served)?.model_served;
  const total = calls.reduce((a, c) => a + (c.cost_usd ?? 0), 0);
  const fb = route?.fallbacks?.[0];
  const panel = `step-${step.id}-detail`;
  return (
    <div className={cn("relative overflow-hidden rounded-xl border bg-surface-1", display === "failed" ? "border-urgent" : "border-line", slide && "animate-strip-in")}>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={open ? panel : undefined}
        onClick={() => onToggle(step.id)}
        className="block w-full px-4 py-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent"
      >
        <span className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <StepMark state={display} />
          <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink" title={step.title}>
            {step.title}
          </span>
          {served && <Chip>Model {served}</Chip>}
          {elapsed && (
            <span className="mono text-muted" title={live ? "Running for" : "Run time"}>
              {elapsed}
            </span>
          )}
          <span className="mono text-muted">{calls.length > 0 ? cost(total) : "—"}</span>
        </span>
        {(route || waiting || errors.length > 0 || ((display === "skipped" || display === "failed") && step.detail)) && (
          <span className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
            {route && <Chip>Routed by {route.engine}</Chip>}
            {fb && (
              <span>
                {words(fb.kind)}
                {fb.status ? ` (${fb.status})` : ""}
              </span>
            )}
            {waiting && (
              <span className="text-ink-muted">
                {waitingLabel(waiting)}
                {waitFor && ` · ${waitFor}`}
              </span>
            )}
            {(display === "skipped" || display === "failed") && step.detail && <span className="min-w-0 truncate">{step.detail}</span>}
            {errors[0] && <span className="text-urgent">{plainError(errors[0].code)}</span>}
          </span>
        )}
      </button>
      {display === "running" && (
        <span className="absolute inset-x-0 bottom-0 h-px overflow-hidden" aria-hidden>
          <span className="block h-px w-1/3 animate-run-line bg-accent" />
        </span>
      )}
      {open && (
        <div id={panel} className="space-y-2 border-t border-line px-4 py-3">
          {errors.map((e) => (
            <ErrorBlock key={e.id} e={e} />
          ))}
          {route && (
            <p className="mono text-xs text-muted">
              Router: {route.engine}
              {route.model && ` · ${route.model}`}
              {route.confidence != null && ` · confidence ${route.confidence}`}
              {route.ms != null && ` · ${route.ms} ms`}
            </p>
          )}
          {route?.fallbacks?.map((f, i) => (
            <p key={i} className="text-xs text-muted">
              {f.engine} skipped: {words(f.kind)}
              {f.status ? ` (${f.status})` : ""}
              {f.reason ? ` — ${f.reason}` : ""}
            </p>
          ))}
          {waiting?.detail && <p className="text-xs text-muted">{waiting.detail}</p>}
          {calls.map((c) => (
            <CallRow key={c.id} call={c} raw={raw} />
          ))}
          {execs.map((x) => (
            <ExecRow key={x.id} exec={x} />
          ))}
          {calls.length === 0 && execs.length === 0 && errors.length === 0 && (
            <p className="text-xs text-muted">Nothing recorded for this step yet.</p>
          )}
        </div>
      )}
    </div>
  );
}

const same = (a: unknown[], b: unknown[]) => a.length === b.length && a.every((x, i) => x === b[i]);

export const StepCard = memo(
  StepCardView,
  (a, b) =>
    a.step === b.step &&
    a.route === b.route &&
    a.open === b.open &&
    a.now === b.now &&
    a.slide === b.slide &&
    a.raw === b.raw &&
    a.onToggle === b.onToggle &&
    same(a.calls, b.calls) &&
    same(a.execs, b.execs) &&
    same(a.errors, b.errors),
);
