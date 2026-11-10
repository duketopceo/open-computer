import { Icon, type IconName } from "@/components/icons/Icon";
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

const GLYPH: Record<Exclude<StepDisplay, "pending">, IconName> = {
  running: "running",
  waiting: "leased",
  done: "done",
  failed: "failed",
  skipped: "skipped",
};

function Glyph({ state }: { state: StepDisplay }) {
  if (state === "pending") return <Dot state="QUEUED" />;
  return <Icon name={GLYPH[state]} />;
}

export function StepMark({ state, label }: { state: StepDisplay; label?: string }) {
  return (
    <span className={cn("inline-flex shrink-0 items-center gap-1.5", TONE[state], state === "running" && "fx-mark-running")}>
      <Glyph state={state} />
      <span className="mono uppercase tracking-wider">{label ?? WORD[state]}</span>
    </span>
  );
}
