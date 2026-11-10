import { useCallback, useEffect, useState, type ReactNode } from "react";
import type { RunSummary, ThreadSummary } from "@/lib/api";
import { errorMessage } from "@/lib/api";
import { setDevTrace, useDevTrace } from "@/lib/devmode";
import { rel } from "@/lib/format";
import { cn } from "@/lib/cn";
import { NAV } from "@/lib/nav";
import { Icon, type IconName } from "./icons/Icon";
import { NavList } from "./NavList";
import { Brand } from "./PageShell";

// A rail row is the same for a chat and for an older run.
interface Item {
  id: string;
  label: string;
  ts: number;
  archived: boolean;
  /** false while work is still going: no delete offered */
  removable: boolean;
}

const fromThread = (t: ThreadSummary): Item => ({ id: t.id, label: t.title, ts: t.ts, archived: t.archived, removable: !t.working && t.finished });
const fromRun = (r: RunSummary): Item => ({ id: r.run, label: r.goal, ts: r.ts, archived: !!r.archived, removable: r.finished !== false });

export interface Cleanup {
  onDelete: (id: string) => void;
  onArchive: (id: string, archived: boolean) => void;
  onClear: () => void;
}

const RAIL_KEY = "oc.sidebarCollapsed";
const isMobile = () => {
  try {
    return typeof matchMedia === "function" && matchMedia("(max-width: 767px)").matches;
  } catch {
    return false;
  }
};

// Remembered choice wins; with none stored a phone starts as the icon rail.
function initialCollapsed(): boolean {
  try {
    const v = localStorage.getItem(RAIL_KEY);
    if (v === "1" || v === "0") return v === "1";
  } catch {
    /* storage blocked: fall through */
  }
  return isMobile();
}

function useRail() {
  const [collapsed, setState] = useState(initialCollapsed);
  const set = useCallback((next: boolean, persist = true) => {
    setState(next);
    if (!persist) return;
    try {
      localStorage.setItem(RAIL_KEY, next ? "1" : "0");
    } catch {
      /* ignore quota / private mode */
    }
  }, []);
  // Ctrl/Cmd+B toggles from anywhere on the page.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.isComposing || e.altKey || e.shiftKey || !(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== "b") return;
      e.preventDefault();
      setState((c) => {
        const next = !c;
        try {
          localStorage.setItem(RAIL_KEY, next ? "1" : "0");
        } catch {
          /* ignore */
        }
        return next;
      });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return [collapsed, set] as const;
}

/** An icon button for the rail: accessible name, native title and a CSS tooltip on hover or keyboard focus. */
function RailButton({
  label,
  onClick,
  active,
  badge,
  children,
  ...aria
}: {
  label: string;
  onClick: () => void;
  active?: boolean;
  badge?: number;
  children: ReactNode;
} & React.AriaAttributes) {
  return (
    <div className="group relative">
      <button
        type="button"
        onClick={onClick}
        aria-label={badge ? `${label}, ${badge} awaiting approval` : label}
        title={label}
        className={cn(
          "relative flex h-10 w-10 items-center justify-center rounded-lg transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
          active ? "bg-accent-soft text-accent" : "text-ink hover:bg-surface-3",
        )}
        {...aria}
      >
        {children}
        {!!badge && <span aria-hidden className="absolute right-1.5 top-1.5 h-2 w-2 rounded-full bg-accent" />}
      </button>
      <span
        aria-hidden
        className="pointer-events-none absolute left-full top-1/2 z-40 ml-2 hidden -translate-y-1/2 whitespace-nowrap rounded-md border border-line bg-surface-2 px-2 py-1 text-xs text-ink shadow-card group-focus-within:block group-hover:block"
      >
        {label}
      </span>
    </div>
  );
}

export function Sidebar({
  threads,
  runs,
  selected,
  identity,
  onSelect,
  view,
  onAgents,
  pending,
  chats,
  earlier,
}: {
  threads: ThreadSummary[];
  runs: RunSummary[];
  selected: string | null;
  identity: string;
  onSelect: (id: string) => void;
  view: "chat" | "agents";
  onAgents: () => void;
  pending: number;
  chats: Cleanup;
  earlier: Cleanup;
}) {
  const fresh = view === "chat" && !selected;
  const [collapsed, setCollapsed] = useRail();
  // On a phone the open sidebar is an overlay: choosing something puts it away again (not remembered).
  const away = () => {
    if (isMobile()) setCollapsed(true, false);
  };
  const [operatorOpen, setOperatorOpen] = useState(() => {
    try {
      return localStorage.getItem("oc.operatorOpen") === "1";
    } catch {
      return false;
    }
  });
  const toggle = (
    <button
      type="button"
      onClick={() => setCollapsed(!collapsed)}
      aria-expanded={!collapsed}
      aria-controls="app-sidebar"
      aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
      aria-keyshortcuts="Control+B Meta+B"
      title={`${collapsed ? "Expand" : "Collapse"} sidebar (Ctrl+B)`}
      className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-muted hover:bg-surface-3 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
    >
      <Icon name="workspace" />
    </button>
  );

  if (collapsed) {
    return (
      <aside id="app-sidebar" aria-label="Sidebar" data-collapsed="true" className="flex h-full w-14 shrink-0 flex-col items-center gap-1 border-r border-line bg-surface-1 py-3">
        <div className="mb-2">{toggle}</div>
        <RailButton label="New chat" active={fresh} onClick={() => onSelect("")}>
          <Icon name="plus" />
        </RailButton>
        <RailButton label="Agent jobs" active={view === "agents"} badge={pending} aria-current={view === "agents" ? "page" : undefined} onClick={onAgents}>
          <Icon name="approvals" />
        </RailButton>
      </aside>
    );
  }

  return (
    // The wrapper keeps a rail's width in the layout; on a phone the open sidebar floats over the page.
    <div className="relative h-full w-60 shrink-0 max-md:w-14">
    <aside id="app-sidebar" aria-label="Sidebar" data-collapsed="false" className="flex h-full w-60 flex-col border-r border-line bg-surface-1 max-md:absolute max-md:inset-y-0 max-md:left-0 max-md:z-30 max-md:shadow-card">
      <div className="flex items-center justify-between px-4 py-4">
        <Brand />
        {toggle}
      </div>

      <nav className="px-2" aria-label="Everyday">
        <button
          onClick={() => {
            onSelect("");
            away();
          }}
          className={cn(
            "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm font-medium transition-colors",
            fresh ? "bg-accent-soft text-accent" : "text-ink hover:bg-surface-3",
          )}
        >
          <Icon name="plus" /> New chat
        </button>
        <button
          onClick={() => {
            onAgents();
            away();
          }}
          aria-current={view === "agents" ? "page" : undefined}
          className={cn(
            "mt-0.5 flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm font-medium transition-colors",
            view === "agents" ? "bg-accent-soft text-accent" : "text-ink hover:bg-surface-3",
          )}
        >
          <Icon name="approvals" /> Agent jobs
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

      <div className="mt-4 min-h-0 flex-1 overflow-y-auto">
        <Section
          title="Recents"
          empty="No chats yet."
          items={threads.map(fromThread)}
          active={view === "chat" ? selected : null}
          onSelect={(id) => {
            onSelect(id);
            away();
          }}
          cleanup={chats}
        />
        {runs.length > 0 && (
          <details className="mt-3">
            <summary className="cursor-pointer select-none px-4 text-[11px] font-medium uppercase tracking-wider text-muted hover:text-ink">
              Earlier runs ({runs.filter((r) => !r.archived).length})
            </summary>
            <Section
              empty="No runs."
              items={runs.map(fromRun)}
              active={view === "chat" ? selected : null}
              onSelect={onSelect}
              cleanup={earlier}
            />
          </details>
        )}
      </div>

      <div className="border-t border-line px-2 py-2">
        <details
          open={operatorOpen}
          onToggle={(e) => {
            const next = (e.target as HTMLDetailsElement).open;
            setOperatorOpen(next);
            try {
              localStorage.setItem("oc.operatorOpen", next ? "1" : "0");
            } catch {
              /* ignore quota / private mode */
            }
          }}
        >
          <summary className="cursor-pointer select-none px-2.5 py-1 text-[11px] font-medium uppercase tracking-wider text-muted hover:text-ink">
            Operator
          </summary>
          <NavList items={NAV.filter((n) => n.href !== "/")} label="Operator" />
          <DevSwitch />
        </details>
        {identity && (
          <p className="mt-1 truncate px-2.5 text-[11px] text-muted" title={identity}>
            {identity}
          </p>
        )}
      </div>
    </aside>
    </div>
  );
}

function Section({
  title,
  empty,
  items,
  active,
  onSelect,
  cleanup,
}: {
  title?: string;
  empty: string;
  items: Item[];
  active: string | null;
  onSelect: (id: string) => void;
  cleanup: Cleanup;
}) {
  const live = items.filter((i) => !i.archived);
  const archived = items.filter((i) => i.archived);
  const removable = live.filter((i) => i.removable).length;
  const [askClear, setAskClear] = useState(false);
  const row = (i: Item) => (
    <Row key={i.id} item={i} active={active === i.id} onSelect={onSelect} onDelete={cleanup.onDelete} onArchive={cleanup.onArchive} />
  );
  return (
    <section>
      <div className="flex h-5 items-center justify-between px-4">
        {title ? <p className="text-[11px] font-medium uppercase tracking-wider text-muted">{title}</p> : <span />}
        {removable > 0 &&
          (askClear ? (
            <span className="flex items-center gap-2 text-[11px]">
              <button
                onClick={() => {
                  setAskClear(false);
                  cleanup.onClear();
                }}
                className="font-medium text-urgent hover:underline"
              >
                Remove {removable}
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
      <div className="mt-1 px-2">
        {live.length === 0 ? <p className="px-2.5 py-2 text-xs text-muted">{empty}</p> : live.map(row)}
        {archived.length > 0 && (
          <details className="mt-2">
            <summary className="cursor-pointer select-none px-2.5 py-1 text-[11px] text-muted hover:text-ink">
              Archived ({archived.length})
            </summary>
            {archived.map(row)}
          </details>
        )}
      </div>
    </section>
  );
}

// Actions appear on hover or keyboard focus with no delay; delete asks in place.
function Row({
  item,
  active,
  onSelect,
  onDelete,
  onArchive,
}: {
  item: Item;
  active: boolean;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onArchive: (id: string, archived: boolean) => void;
}) {
  const [ask, setAsk] = useState(false);
  if (ask) {
    return (
      <div className="mb-0.5 flex items-center justify-between gap-2 rounded-lg bg-surface-3 px-2.5 py-2 text-[13px]">
        <span className="truncate text-ink">Delete this?</span>
        <span className="flex shrink-0 items-center gap-2">
          <button
            onClick={() => {
              setAsk(false);
              onDelete(item.id);
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
    <div className={cn("group relative mb-0.5 rounded-lg transition-colors", active ? "bg-accent-soft text-accent" : "text-ink hover:bg-surface-3")}>
      <button onClick={() => onSelect(item.id)} className="flex w-full items-center justify-between gap-2 px-2.5 py-2 text-left">
        <span className={cn("truncate text-[13px]", item.archived && "text-muted")}>{item.label}</span>
        <span className="mono shrink-0 text-[10px] text-muted group-focus-within:invisible group-hover:invisible">{rel(item.ts)}</span>
      </button>
      <span className="absolute inset-y-0 right-1 hidden items-center gap-0.5 group-focus-within:flex group-hover:flex">
        <IconButton label={item.archived ? "Restore" : "Archive"} icon="archive" onClick={() => onArchive(item.id, !item.archived)} />
        {item.removable && (
          <IconButton label="Delete" icon="delete" onClick={() => setAsk(true)} />
        )}
      </span>
    </div>
  );
}

function IconButton({ label, onClick, icon }: { label: string; onClick: () => void; icon: IconName }) {
  return (
    <button
      onClick={onClick}
      aria-label={label}
      title={label}
      className="inline-flex h-6 w-6 items-center justify-center rounded-md text-muted hover:bg-surface-4 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
    >
      <Icon name={icon} />
    </button>
  );
}

// Dev trace records program text, command output, plan JSON and model reasoning for new runs, so
// turning it on asks first; turning it off does not.
function DevSwitch() {
  const dev = useDevTrace();
  const on = dev.active;
  const [ask, setAsk] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const set = (v: boolean) => {
    setBusy(true);
    setErr("");
    setDevTrace(v)
      .then(() => setAsk(false))
      .catch((e) => setErr(errorMessage(e, "Could not change Dev trace.")))
      .finally(() => setBusy(false));
  };
  return (
    <div className="mt-1 px-2.5 py-1.5">
      <button
        type="button"
        role="switch"
        aria-checked={on}
        disabled={busy || dev.env}
        onClick={() => (on ? set(false) : setAsk(true))}
        className="flex w-full items-center justify-between gap-2 rounded-lg text-sm text-muted hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-50"
      >
        <span>Dev trace</span>
        <span className="mono uppercase tracking-wider">{on ? "On" : "Off"}</span>
      </button>
      {dev.env && <p className="mt-1 text-xs text-muted">Forced on by the environment (OC_DEV_TRACE).</p>}
      {ask && !on && (
        <div role="alertdialog" aria-label="Turn on Dev trace" className="mt-2 rounded-lg border border-line bg-surface-2 p-2.5 text-xs text-ink">
          <p>
            Dev trace captures content for new runs: program text, command output, plan JSON and model reasoning. Secrets are redacted
            before they are written, but the rest is stored on this machine.
          </p>
          <div className="mt-2 flex items-center gap-3">
            <button disabled={busy} onClick={() => set(true)} className="font-medium text-ink underline underline-offset-2 hover:text-accent">
              Turn on
            </button>
            <button disabled={busy} onClick={() => setAsk(false)} className="text-muted hover:text-ink">
              Cancel
            </button>
          </div>
        </div>
      )}
      {err && (
        <p className="mt-1 text-xs text-urgent" role="alert">
          {err}
        </p>
      )}
    </div>
  );
}
