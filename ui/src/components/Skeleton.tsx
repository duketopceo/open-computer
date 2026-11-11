import { cn } from "@/lib/cn";

/** Placeholder strip rows for a list that is loading. Static on purpose (DESIGN.md: no indeterminate motion); the
 *  word "Loading" beside it carries the state. */
export function Skeleton({ rows = 3, className }: { rows?: number; className?: string }) {
  return (
    <div className={cn("space-y-2", className)} aria-hidden data-testid="skeleton">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex h-12 items-center gap-3 rounded-lg border border-line bg-surface-1 px-3">
          <span className="h-3 w-3 border border-line-strong" />
          <span className="h-2.5 flex-1 max-w-xs rounded-sm bg-surface-3" />
          <span className="ml-auto h-2.5 w-12 rounded-sm bg-surface-3" />
        </div>
      ))}
    </div>
  );
}
