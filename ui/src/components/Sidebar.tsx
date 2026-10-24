import type { RunSummary } from "@/lib/api";
import { rel } from "@/lib/format";
import { cn } from "@/lib/cn";

const OPERATOR = [
  { label: "Devices", href: "/devices" },
  { label: "Sandboxes", href: "/sandboxes" },
  { label: "Approvals", href: "/approvals" },
  { label: "Router", href: "/router" },
  { label: "Audit", href: "/audit" },
];

export function Sidebar({
  runs,
  selected,
  identity,
  onSelect,
}: {
  runs: RunSummary[];
  selected: string | null;
  identity: string;
  onSelect: (id: string) => void;
}) {
  return (
    <aside className="flex h-full w-60 shrink-0 flex-col border-r border-line bg-white/60">
      <div className="flex items-center gap-2 px-4 py-4">
        <span className="grid h-6 w-6 place-items-center rounded-md bg-teal text-white">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden>
            <path
              d="M4 5h16v12H4z M8 9h8M8 12h5"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </span>
        <span className="text-sm font-semibold text-ink">Open Computer</span>
      </div>

      <nav className="px-2">
        <button
          onClick={() => onSelect("")}
          className={cn(
            "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm font-medium transition-colors",
            selected === "" || selected === null
              ? "bg-teal-soft text-teal"
              : "text-ink hover:bg-black/5",
          )}
        >
          <CmdIcon /> Computer
        </button>
      </nav>

      <div className="mt-4 flex min-h-0 flex-1 flex-col">
        <p className="px-4 text-[11px] font-medium uppercase tracking-wider text-muted">
          Runs
        </p>
        <div className="mt-1 flex-1 overflow-y-auto px-2">
          {runs.length === 0 ? (
            <p className="px-2.5 py-2 text-xs text-muted">No runs yet.</p>
          ) : (
            runs.map((r) => (
              <button
                key={r.run}
                onClick={() => onSelect(r.run)}
                className={cn(
                  "mb-0.5 flex w-full items-center justify-between gap-2 rounded-lg px-2.5 py-2 text-left transition-colors",
                  selected === r.run ? "bg-teal-soft text-teal" : "text-ink hover:bg-black/5",
                )}
              >
                <span className="truncate text-[13px]">{r.goal}</span>
                <span className="mono shrink-0 text-[10px] text-muted">{rel(r.ts)}</span>
              </button>
            ))
          )}
        </div>
      </div>

      <div className="border-t border-line px-2 py-3">
        <p className="px-2.5 pb-1 text-[11px] font-medium uppercase tracking-wider text-muted">
          Operator
        </p>
        {OPERATOR.map((o) => (
          <a
            key={o.href}
            href={o.href}
            className="flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-sm text-muted hover:bg-black/5 hover:text-ink"
          >
            {o.label}
          </a>
        ))}
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