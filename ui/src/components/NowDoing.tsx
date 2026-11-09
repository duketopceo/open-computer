import { useEffect, useId, useState, type ReactNode } from "react";
import { cn } from "@/lib/cn";
import { dur } from "@/lib/format";
import type { NowDoing as Now } from "@/lib/nowDoing";

/** A live run condensed to one line: "Now: <what> · 12s". Click to follow everything (the steps and, when the run
 *  has a stream, the live timeline); click again to fold it back. */
export function NowDoing({ now, children }: { now: Now; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const panel = useId();
  const [, tick] = useState(0);
  useEffect(() => {
    if (now.since == null) return;
    const t = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [now.since]);
  const took = now.since != null ? dur(now.since, Date.now() / 1000) : "";
  return (
    <div>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panel}
        onClick={() => setOpen((o) => !o)}
        className="group flex w-full items-center gap-2 rounded-lg border border-line bg-surface-2 px-3 py-2 text-left text-sm hover:bg-surface-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        <span aria-hidden="true" className={cn("fx-now-doing inline-block h-1.5 w-1.5 shrink-0 rounded-full", now.needsYou ? "bg-attention" : "bg-accent")} />
        <span className="min-w-0 flex-1 truncate">
          <span className={cn("font-medium", now.needsYou ? "text-attention" : "text-ink")}>{now.needsYou ? "" : "Now: "}</span>
          <span className="text-ink-muted">{now.text}</span>
          {took && <span className="mono text-muted"> · {took}</span>}
        </span>
        <span className="shrink-0 text-xs text-muted group-hover:text-ink">{open ? "Hide" : "Follow"}</span>
      </button>
      <div id={panel} hidden={!open} className="mt-3">
        {open && children}
      </div>
    </div>
  );
}
