import { cn } from "@/lib/cn";
import type { StepDisplay } from "@/lib/runEvents";
import { Dot } from "../primitives";

// Glyph plus word for a step's display state (DESIGN.md: state is never colour alone).
// Only a running step uses the accent ("live or needs you"). Waiting uses the leased dash and
// skipped a muted dashed box; neither is accent.
const WORD: Record<StepDisplay, string> = {
  pending: "Pending",
  running: "Running",
  waiting: "Waiting",
  done: "Done",
  failed: "Failed",
  skipped: "Skipped",
};

const TONE: Record<StepDisplay, string> = {
  pending: "text-muted",
  running: "text-accent",
  waiting: "text-ink-muted",
  done: "text-healthy",
  failed: "text-urgent",
  skipped: "text-muted",
};

function Glyph({ state }: { state: StepDisplay }) {
  if (state === "pending") return <Dot state="QUEUED" />;
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="square" aria-hidden>
      {state === "running" && <path d="M5 3.5v9l7-4.5z" fill="currentColor" />}
      {state === "waiting" && <path d="M3.5 8h9" />}
      {state === "done" && <path d="M3.5 8.5l3 3 6-7" />}
      {state === "failed" && <path d="M4 4l8 8M12 4l-8 8" />}
      {state === "skipped" && <rect x="2.5" y="2.5" width="11" height="11" strokeDasharray="2.5 2" />}
    </svg>
  );
}

export function StepMark({ state, label }: { state: StepDisplay; label?: string }) {
  return (
    <span className={cn("inline-flex shrink-0 items-center gap-1.5", TONE[state])}>
      <Glyph state={state} />
      <span className="mono uppercase tracking-wider">{label ?? WORD[state]}</span>
    </span>
  );
}
