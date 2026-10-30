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
        "inline-flex h-9 items-center gap-1.5 rounded-lg px-3 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal/40 disabled:opacity-50",
        variant === "primary" && "bg-teal text-white hover:bg-teal/90",
        variant === "ghost" && "text-ink hover:bg-black/5",
        variant === "subtle" && "border border-line bg-white text-ink hover:bg-black/[.03]",
        className,
      )}
      {...props}
    />
  );
});

const STATE_STYLE: Record<TaskState, string> = {
  QUEUED: "text-muted",
  LEASED: "text-amber-600",
  RUNNING: "text-amber-600",
  DONE: "text-teal",
  FAILED: "text-red-600",
  "AWAITING-APPROVAL": "text-violet-600",
};

export function StateWord({ state }: { state: TaskState }) {
  return (
    <span className={cn("mono uppercase tracking-wider", STATE_STYLE[state] ?? "text-muted")}>
      {state.replace("AWAITING-APPROVAL", "AWAITING")}
    </span>
  );
}

export function Dot({ state }: { state: TaskState }) {
  const color: Record<TaskState, string> = {
    QUEUED: "bg-muted/40",
    LEASED: "bg-amber-500",
    RUNNING: "bg-amber-500",
    DONE: "bg-teal",
    FAILED: "bg-red-500",
    "AWAITING-APPROVAL": "bg-violet-500",
  };
  return <span className={cn("inline-block h-1.5 w-1.5 rounded-full", color[state])} />;
}

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn("rounded-2xl border border-line bg-white shadow-card", className)}>
      {children}
    </div>
  );
}

export function Chip({ children, active }: { children: ReactNode; active?: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex h-6 items-center rounded-full border px-2.5 text-xs font-medium",
        active ? "border-teal/30 bg-teal-soft text-teal" : "border-line text-muted",
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
  running: { label: "running", style: "text-amber-700" },
  finished: { label: "finished", style: "text-teal" },
  stalled: { label: "stalled", style: "text-amber-700" },
  rejected: { label: "rejected", style: "text-red-700" },
  budget_exhausted: { label: "budget exhausted", style: "text-red-700" },
  max_rounds: { label: "max rounds", style: "text-amber-700" },
  failed: { label: "failed", style: "text-red-700" },
};

export function AgentStatusWord({ status }: { status: AgentStatus }) {
  const s = AGENT_STATUS[status] ?? { label: String(status), style: "text-muted" };
  return <span className={cn("mono uppercase tracking-wider", s.style)}>{s.label}</span>;
}
