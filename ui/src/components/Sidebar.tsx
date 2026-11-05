import { useState } from "react";
import type { RunSummary } from "@/lib/api";
import { rel } from "@/lib/format";
import { cn } from "@/lib/cn";
import { NAV } from "@/lib/nav";
import { NavList } from "./NavList";
import { Brand } from "./PageShell";

export function Sidebar({
  runs,
  selected,
  identity,
  onSelect,
  view,
  onAgents,
  pending,
  onDelete,
  onArchive,
  onClear,
}: {
  runs: RunSummary[];
  selected: string | null;
  identity: string;
  onSelect: (id: string) => void;
  view: "runs" | "agents";
  onAgents: () => void;
  pending: number;
  onDelete: (id: string) => void;
  onArchive: (id: string, archived: boolean) => void;
  onClear: () => void;
}) {
  const live = runs.filter((r) => !r.archived);
  const archived = runs.filter((r) => r.archived);
  const finished = live.filter((r) => r.finished).length;
  const [askClear, setAskClear] = useState(false);
  return (
    <aside className="flex h-full w-60 shrink-0 flex-col border-r border-line bg-surface-1">
      <div className="px-4 py-4">
        <Brand />
      </div>

      <nav className="px-2">
        <button
          onClick={() => onSelect("")}
          className={cn(
            "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm font-medium transition-colors",
            view === "runs" && (selected === "" || selected === null)
              ? "bg-accent-soft text-accent"
              : "text-ink hover:bg-surface-3",
          )}
        >
          <CmdIcon /> Computer
        </button>
        <button
          onClick={onAgents}
          aria-current={view === "agents" ? "page" : undefined}
          className={cn(
            "mt-0.5 flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm font-medium transition-colors",
            view === "agents" ? "bg-accent-soft text-accent" : "text-ink hover:bg-surface-3",
          )}
        >
          <CmdIcon /> Agent jobs
          {pending > 0 && (
            <span
              className="ml-auto rounded-full bg-accent-soft px-1.5 text-[11px] font-semibold text-ink"
              aria-label={`${pending} awaiting approval`}
            >
              {pending}
            </span>
          )}
        </button>
      </nav>

      <div className="mt-4 flex min-h-0 flex-1 flex-col">
        <div className="flex h-5 items-center justify-between px-4">
          <p className="text-[11px] font-medium uppercase tracking-wider text-muted">Runs</p>
          {finished > 0 &&
            (askClear ? (
              <span className="flex items-center gap-2 text-[11px]">
                <button
                  onClick={() => {
                    setAskClear(false);
                    onClear();
                  }}
                  className="font-medium text-urgent hover:underline"
                >
                  Remove {finished}
                </button>
                <button onClick={() => setAskClear(false)} className="text-muted hover:text-ink">
                  Keep
                </button>
              </span>
            ) : (
              <button onClick={() => setAskClear(true)} className="text-[11px] text-muted hover:text-ink">
                Clear finished
              </button>
            ))}
        </div>
        <div className="mt-1 flex-1 overflow-y-auto px-2">
          {live.length === 0 ? (
            <p className="px-2.5 py-2 text-xs text-muted">No runs yet.</p>
          ) : (
            live.map((r) => (
              <RunRow
                key={r.run}
                r={r}
                active={view === "runs" && selected === r.run}
                onSelect={onSelect}
                onDelete={onDelete}
                onArchive={onArchive}
              />
            ))
          )}
          {archived.length > 0 && (
            <details className="mt-2">
              <summary className="cursor-pointer select-none px-2.5 py-1 text-[11px] text-muted hover:text-ink">
                Archived ({archived.length})
              </summary>
              {archived.map((r) => (
                <RunRow
                  key={r.run}
                  r={r}
                  active={view === "runs" && selected === r.run}
                  onSelect={onSelect}
                  onDelete={onDelete}
                  onArchive={onArchive}
                />
              ))}
            </details>
          )}
        </div>
      </div>

      <div className="border-t border-line px-2 py-3">
        <p className="px-2.5 pb-1 text-[11px] font-medium uppercase tracking-wider text-muted">
          Operator
        </p>
        <NavList items={NAV.filter((n) => n.href !== "/")} label="Operator" />
        {identity && (
          <p className="mt-2 truncate px-2.5 text-[11px] text-muted" title={identity}>
            {identity}
          </p>
        )}
      </div>
    </aside>
  );
}

function CmdIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden>
      <rect x="4" y="4" width="16" height="16" rx="3" stroke="currentColor" strokeWidth="1.6" />
      <circle cx="12" cy="12" r="3.5" stroke="currentColor" strokeWidth="1.6" />
    </svg>
  );
}

// One rail row. Actions appear on hover or keyboard focus, with no delay; delete asks in place.
function RunRow({
  r,
  active,
  onSelect,
  onDelete,
  onArchive,
}: {
  r: RunSummary;
  active: boolean;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onArchive: (id: string, archived: boolean) => void;
}) {
  const [ask, setAsk] = useState(false);
  const busy = r.finished === false;
  if (ask) {
    return (
      <div className="mb-0.5 flex items-center justify-between gap-2 rounded-lg bg-surface-3 px-2.5 py-2 text-[13px]">
        <span className="truncate text-ink">Delete this run?</span>
        <span className="flex shrink-0 items-center gap-2">
          <button
            onClick={() => {
              setAsk(false);
              onDelete(r.run);
            }}
            className="font-medium text-urgent hover:underline"
          >
            Delete
          </button>
          <button onClick={() => setAsk(false)} className="text-muted hover:text-ink">
            Keep
          </button>
        </span>
      </div>
    );
  }
  return (
    <div
      className={cn(
        "group relative mb-0.5 rounded-lg transition-colors",
        active ? "bg-accent-soft text-accent" : "text-ink hover:bg-surface-3",
      )}
    >
      <button onClick={() => onSelect(r.run)} className="flex w-full items-center justify-between gap-2 px-2.5 py-2 text-left">
        <span className={cn("truncate text-[13px]", r.archived && "text-muted")}>{r.goal}</span>
        <span className="mono shrink-0 text-[10px] text-muted group-focus-within:invisible group-hover:invisible">
          {rel(r.ts)}
        </span>
      </button>
      <span className="absolute inset-y-0 right-1 hidden items-center gap-0.5 group-focus-within:flex group-hover:flex">
        <IconButton label={r.archived ? "Restore run" : "Archive run"} onClick={() => onArchive(r.run, !r.archived)}>
          <path d="M4 7h16v3H4zM6 10v8h12v-8M10 14h4" />
        </IconButton>
        {!busy && (
          <IconButton label="Delete run" onClick={() => setAsk(true)}>
            <path d="M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12" />
          </IconButton>
        )}
      </span>
    </div>
  );
}

function IconButton({ label, onClick, children }: { label: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      aria-label={label}
      title={label}
      className="inline-flex h-6 w-6 items-center justify-center rounded-md text-muted hover:bg-surface-4 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
    >
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        {children}
      </svg>
    </button>
  );
}
