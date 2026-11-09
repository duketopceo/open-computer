import { useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError, type ThreadDetail } from "@/lib/api";
import { cn } from "@/lib/cn";
import {
  applyEvents,
  emptyTimeline,
  getRunEvents,
  type Timeline,
} from "@/lib/runEvents";
import {
  activity,
  callLine,
  threadSources,
  type Activity,
} from "@/lib/sidePanel";

const KEY = "oc-side-panel"; // "open" | "closed"; absent means open on wide screens
const POLL_MS = 2000;

/** The thread's runs' event streams (only runs traced with Dev trace have one): fetched from a cursor, polled
 *  while the thread works, dropped quietly on 404. */
function useStreams(
  runIds: string[],
  live: boolean,
): { byRun: Record<string, Timeline>; traced: boolean } {
  const [byRun, setByRun] = useState<Record<string, Timeline>>({});
  const cursors = useRef<Record<string, number | "off" | "done">>({});
  const key = runIds.join(",");
  useEffect(() => {
    let dead = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      await Promise.all(
        runIds.map(async (id) => {
          const at = cursors.current[id] ?? 0;
          if (at === "off" || at === "done") return;
          try {
            const r = await getRunEvents(id, at);
            if (dead) return;
            cursors.current[id] = r.finished
              ? "done"
              : r.events.length
                ? r.events[r.events.length - 1].seq
                : at;
            if (r.events.length)
              setByRun((p) => ({
                ...p,
                [id]: applyEvents(p[id] ?? emptyTimeline(), r.events),
              }));
          } catch (e) {
            if (e instanceof ApiError && e.status === 404)
              cursors.current[id] = "off";
          }
        }),
      );
      if (!dead && live) timer = setTimeout(tick, POLL_MS);
    };
    tick();
    return () => {
      dead = true;
      clearTimeout(timer);
    };
    // keyed on the id list, not the array identity
  }, [key, live]);
  return { byRun, traced: Object.keys(byRun).length > 0 };
}

function initialOpen(): boolean {
  try {
    const v = localStorage.getItem(KEY);
    if (v) return v === "open";
  } catch {
    /* storage off: fall through */
  }
  return (
    typeof window !== "undefined" &&
    !!window.matchMedia?.("(min-width: 1024px)").matches
  );
}

export interface SidePanelState {
  sources: ReturnType<typeof threadSources>;
  web: Activity[];
  tools: Activity[];
  traced: boolean;
  total: number;
  open: boolean;
  toggle: (open: boolean) => void;
}

/** What the chat's right side shows, and whether it is open. Shared by the toggle and the panel. */
export function useSidePanel(thread: ThreadDetail): SidePanelState {
  const runIds = Object.keys(thread.runs).filter((id) => thread.runs[id]);
  const { byRun, traced } = useStreams(runIds, thread.working);
  const sources = threadSources(thread);
  const acts = runIds.map((id) =>
    byRun[id] ? activity(id, byRun[id]) : { web: [], tools: [] },
  );
  const web = acts.flatMap((a) => a.web);
  const tools = acts.flatMap((a) => a.tools);
  const [open, setOpen] = useState(initialOpen);
  const toggle = (v: boolean) => {
    setOpen(v);
    try {
      localStorage.setItem(KEY, v ? "open" : "closed");
    } catch {
      /* storage off: the choice lasts this view */
    }
  };
  return {
    sources,
    web,
    tools,
    traced,
    total: sources.length + web.length + tools.length,
    open,
    toggle,
  };
}

/** The folded panel: one quiet button with the count, in the conversation's top row. */
export function SidePanelToggle({ side }: { side: SidePanelState }) {
  if (side.total === 0 || side.open) return null;
  return (
    <button
      type="button"
      onClick={() => side.toggle(true)}
      aria-expanded={false}
      aria-controls="side-panel"
      className="inline-flex h-7 items-center gap-1.5 rounded-full border border-line bg-surface-1 px-3 text-xs font-medium text-ink hover:bg-surface-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
    >
      Sources and tools
      <span className="mono text-muted">{side.total}</span>
    </button>
  );
}

/** The chat's right side: sources the answers cited, the web searches and page reads behind them, and the other
 *  tool calls. Collapsible; the choice is remembered in this browser. Absent while there is nothing to show. */
export function SidePanel({ side }: { side: SidePanelState }) {
  const { sources, web, tools, traced } = side;
  if (side.total === 0 || !side.open) return null;
  return (
    <aside
      id="side-panel"
      aria-label="Sources and tools"
      className="fixed inset-y-0 right-0 z-30 flex w-80 max-w-[90vw] flex-col border-l border-line bg-surface-1 shadow-card lg:static lg:z-auto lg:max-w-none lg:shadow-none"
    >
      <div className="flex items-center justify-between border-b border-line px-4 py-3">
        <h2 className="text-sm font-medium text-ink">Sources and tools</h2>
        <button
          type="button"
          onClick={() => side.toggle(false)}
          aria-expanded={true}
          aria-controls="side-panel"
          className="rounded-md px-2 py-1 text-xs text-muted hover:bg-surface-3 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          Hide
        </button>
      </div>
      <div className="flex-1 space-y-6 overflow-y-auto px-4 py-4 text-sm">
        <Section
          title="Sources"
          count={sources.length}
          empty="No sources cited yet."
        >
          {sources.map((s) => (
            <li key={s.url} className="min-w-0">
              <a
                href={s.url}
                target="_blank"
                rel="noreferrer noopener"
                className="group flex gap-2 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                <span className="mono shrink-0 text-muted">[{s.n}]</span>
                <span className="min-w-0">
                  <span className="block truncate font-medium text-ink group-hover:underline">
                    {s.host}
                  </span>
                  <span className="block truncate text-xs text-muted">
                    {s.url.replace(/^https?:\/\/(www\.)?/, "")}
                  </span>
                </span>
              </a>
            </li>
          ))}
        </Section>
        <Section
          title="Web searches"
          count={web.length}
          empty={
            traced ? "No web searches." : "Turn on Dev trace to list them."
          }
        >
          {web.map((a) => (
            <Row key={a.id} a={a} />
          ))}
        </Section>
        <Section
          title="Tool calls"
          count={tools.length}
          empty={traced ? "No tool calls." : "Turn on Dev trace to list them."}
        >
          {tools.map((a) => (
            <Row key={a.id} a={a} mono />
          ))}
        </Section>
      </div>
    </aside>
  );
}

function Section({
  title,
  count,
  empty,
  children,
}: {
  title: string;
  count: number;
  empty: string;
  children: ReactNode;
}) {
  return (
    <section>
      <h3 className="mb-2 flex items-baseline gap-2 text-xs font-medium uppercase tracking-wider text-muted">
        {title}
        <span className="mono normal-case">{count}</span>
      </h3>
      {count ? (
        <ul className="space-y-2">{children}</ul>
      ) : (
        <p className="text-xs text-muted">{empty}</p>
      )}
    </section>
  );
}

function Row({ a, mono = false }: { a: Activity; mono?: boolean }) {
  const s = a.call.status;
  return (
    <li className="flex min-w-0 items-start gap-2">
      <span
        aria-hidden="true"
        className={cn(
          "mt-1.5 inline-block h-1.5 w-1.5 shrink-0 rounded-full",
          s === "error"
            ? "bg-urgent"
            : s === "running"
              ? "bg-attention"
              : "bg-accent",
        )}
      />
      <span className="min-w-0">
        <span
          className={cn("block truncate text-ink", mono && "mono text-xs")}
          title={a.call.summary || a.call.command}
        >
          {callLine(a.call)}
        </span>
        <span className="block truncate text-xs text-muted">
          {[
            a.step,
            s === "error" ? "failed" : s === "running" ? "running" : "",
            a.call.duration_ms != null
              ? `${(a.call.duration_ms / 1000).toFixed(1)}s`
              : "",
          ]
            .filter(Boolean)
            .join(" · ")}
        </span>
      </span>
    </li>
  );
}
