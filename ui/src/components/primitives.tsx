import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { cn } from "@/lib/cn";
import type { AgentStatus, TaskState } from "@/lib/api";

export const Button = forwardRef<
  HTMLButtonElement,
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "ghost" | "subtle" }
>(function Button({ className, variant = "ghost", ...props }, ref) {
  return (
    <button
      ref={ref}
      className={cn(
        "inline-flex h-9 items-center gap-1.5 rounded-lg px-3 text-sm font-medium transition-colors max-md:h-10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-50",
        variant === "primary" && "bg-accent text-on-accent hover:bg-accent-hover",
        variant === "ghost" && "text-ink hover:bg-surface-3",
        variant === "subtle" && "border border-line bg-surface-1 text-ink hover:bg-surface-2",
        className,
      )}
      {...props}
    />
  );
});

const STATE_STYLE: Record<TaskState, string> = {
  QUEUED: "text-muted",
  LEASED: "text-attention",
  RUNNING: "text-attention",
  DONE: "text-accent",
  FAILED: "text-urgent",
  "AWAITING-APPROVAL": "text-accent",
};

/** Plain words for the engine's states: a leased step is just starting, an awaiting one needs the owner. */
const STATE_WORD: Partial<Record<TaskState, string>> = { LEASED: "STARTING", "AWAITING-APPROVAL": "AWAITING" };

export function StateWord({ state }: { state: TaskState }) {
  return (
    <span className={cn("mono uppercase tracking-wider", STATE_STYLE[state] ?? "text-muted")}>
      {STATE_WORD[state] ?? state}
    </span>
  );
}

export function Dot({ state }: { state: TaskState }) {
  const color: Record<TaskState, string> = {
    QUEUED: "bg-surface-4",
    LEASED: "bg-attention",
    RUNNING: "bg-attention",
    DONE: "bg-accent",
    FAILED: "bg-urgent-soft0",
    "AWAITING-APPROVAL": "bg-accent",
  };
  return <span className={cn("inline-block h-1.5 w-1.5 rounded-full", color[state])} />;
}

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn("rounded-2xl border border-line bg-surface-1 shadow-card", className)}>
      {children}
    </div>
  );
}

export function Chip({
  children,
  active,
  filled,
  title,
}: {
  children: ReactNode;
  active?: boolean;
  /** A neutral solid chip, for a fact about the row (the model) rather than a state. */
  filled?: boolean;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={cn(
        "inline-flex h-6 items-center rounded-full border px-2.5 text-xs font-medium",
        active
          ? "border-accent bg-accent-soft text-accent"
          : filled
            ? "border-transparent bg-surface-3 text-ink"
            : "border-line text-muted",
      )}
    >
      {children}
    </span>
  );
}

// Agent-job statuses are not run TaskStates; they get their own map. `queued`
// means submitted but no runner is configured, so it says so.
const AGENT_STATUS: Record<AgentStatus, { label: string; style: string }> = {
  queued: { label: "queued · not running", style: "text-muted" },
  running: { label: "running", style: "text-attention" },
  finished: { label: "finished", style: "text-accent" },
  stalled: { label: "stalled", style: "text-attention" },
  rejected: { label: "rejected", style: "text-urgent" },
  budget_exhausted: { label: "budget exhausted", style: "text-urgent" },
  max_rounds: { label: "max rounds", style: "text-attention" },
  failed: { label: "failed", style: "text-urgent" },
};

export function AgentStatusWord({ status }: { status: AgentStatus }) {
  const s = AGENT_STATUS[status] ?? { label: String(status), style: "text-muted" };
  return <span className={cn("mono uppercase tracking-wider", s.style)}>{s.label}</span>;
}
