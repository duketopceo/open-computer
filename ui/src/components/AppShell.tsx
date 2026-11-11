import { useEffect, useRef, useState, type ReactNode } from "react";
import { session, type RunSummary } from "@/lib/api";
import { useDeveloperMode } from "@/lib/developer";
import { rel } from "@/lib/format";
import { cn } from "@/lib/cn";
import { DEVELOPER, PRIMARY, isDeveloperPath } from "@/lib/nav";
import { Link, useRouter } from "@/lib/router";
import { useShellData } from "@/lib/useShellData";
import { Brand } from "./Brand";
import { NavList } from "./NavList";

export interface ShellHome {
  /** The home page's own polled run list (avoids a second fetch). */
  runs?: RunSummary[];
  selectedRun?: string | null;
  onNew?: () => void;
  onPickRun?: (id: string) => void;
}

const money = (n: number) => (n > 0 && n < 0.01 ? "<$0.01" : `$${n.toFixed(2)}`);

/** The one shell for every React page: left nav on desktop, top bar and slide-in menu under 768 px. */
export function AppShell({
  title,
  mainClassName,
  home = {},
  children,
}: {
  title: string;
  mainClassName?: string;
  home?: ShellHome;
  children: ReactNode;
}) {
  const { path, search, navs } = useRouter();
  const [open, setOpen] = useState(false);
  const main = useRef<HTMLElement>(null);
  const menuBtn = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    document.title = `${title} · open-computer`;
  }, [title]);

  // A client-side navigation (a link or back/forward, counted by the router) moves focus to the page;
  // the first render of a document is a real page load, where the browser already did. Only a page
  // change counts: a query-string change on the same page (the activity log filter) must not steal focus.
  useEffect(() => {
    if (navs > 0) main.current?.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path]);

  useEffect(() => setOpen(false), [path, search]);

  // The menu is modal on a phone: the page behind it is inert, Esc closes, focus goes in and comes back.
  useEffect(() => {
    const m = main.current;
    if (!open) {
      m?.removeAttribute("inert");
      return;
    }
    m?.setAttribute("inert", "");
    panel.current?.querySelector<HTMLElement>("a,button")?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        menuBtn.current?.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      m?.removeAttribute("inert");
    };
  }, [open]);

  return (
    <div className="flex h-full flex-col md:flex-row">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded-lg focus:bg-white focus:px-3 focus:py-2 focus:text-sm focus:shadow-card"
      >
        Skip to content
      </a>

      <aside className="hidden h-full w-60 shrink-0 border-r border-line bg-white/60 md:flex">
        <SidebarBody home={home} />
      </aside>

      <header className="flex items-center justify-between gap-2 border-b border-line bg-white/60 px-4 py-2.5 md:hidden">
        <Brand />
        <div className="flex items-center gap-2">
          <Link
            href="/"
            onClick={home.onNew}
            className="inline-flex h-9 items-center rounded-lg bg-teal px-3 text-sm font-medium text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal/40"
          >
            New
          </Link>
          <button
            ref={menuBtn}
            type="button"
            aria-expanded={open}
            aria-controls="mobile-nav"
            onClick={() => setOpen((o) => !o)}
            className="h-9 rounded-lg border border-line bg-white px-3 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal/40"
          >
            {open ? "Close menu" : "Menu"}
          </button>
        </div>
      </header>

      {open && (
        <>
          <button
            type="button"
            tabIndex={-1}
            aria-hidden
            onClick={() => setOpen(false)}
            className="fixed inset-0 z-30 bg-black/30 md:hidden"
          />
          <div
            id="mobile-nav"
            ref={panel}
            className="fixed inset-y-0 left-0 z-40 flex w-72 max-w-[85vw] flex-col border-r border-line bg-canvas shadow-composer md:hidden"
          >
            <SidebarBody home={home} />
          </div>
        </>
      )}

      <main
        id="main"
        ref={main}
        tabIndex={-1}
        className={cn("min-w-0 flex-1 overflow-y-auto focus:outline-none", mainClassName)}
      >
        {children}
      </main>
    </div>
  );
}

function SidebarBody({ home }: { home: ShellHome }) {
  const { path } = useRouter();
  const data = useShellData(home.runs);
  const [developer, setDeveloper] = useDeveloperMode();
  const [identity, setIdentity] = useState("");
  const showDev = developer || isDeveloperPath(path);
  const recent = data.runs.slice(0, 8);

  useEffect(() => {
    session()
      .then((s) => setIdentity(s.identity))
      .catch(() => {});
  }, []);

  return (
    <div className="flex h-full w-full flex-col">
      <div className="px-4 py-4">
        <Brand />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2">
        <NavList
          items={PRIMARY}
          label="Console"
          badges={{ "/approvals": { count: data.pending, what: "waiting for you" } }}
          onNew={home.onNew}
          viewingRun={!!home.selectedRun}
        />

        <section aria-labelledby="recent-h" className="mt-5">
          <h2 id="recent-h" className="px-2.5 pb-1 text-[11px] font-medium uppercase tracking-wider text-muted">
            Recent
          </h2>
          {recent.length === 0 ? (
            <p className="px-2.5 py-1 text-xs text-muted">Tasks you start show up here.</p>
          ) : (
            <ul>
              {recent.map((r) => (
                <li key={r.run}>
                  <Link
                    href={`/#${r.run}`}
                    aria-current={home.selectedRun === r.run && path === "/" ? "page" : undefined}
                    onClick={() => home.onPickRun?.(r.run)}
                    className={cn(
                      "flex items-center justify-between gap-2 rounded-lg px-2.5 py-1.5 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal/40",
                      home.selectedRun === r.run && path === "/" ? "bg-teal-soft text-teal" : "text-ink/80 hover:bg-black/5 hover:text-ink",
                    )}
                  >
                    <span className="truncate text-[13px]">{r.goal}</span>
                    <span className="shrink-0 text-[11px] text-muted">{rel(r.ts)}</span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>

        {showDev && (
          <section aria-labelledby="dev-h" className="mt-5 pb-2">
            <h2 id="dev-h" className="px-2.5 pb-1 text-[11px] font-medium uppercase tracking-wider text-muted">
              Developer
            </h2>
            <NavList items={DEVELOPER} label="Developer" />
          </section>
        )}
      </div>

      <div className="space-y-2 border-t border-line px-4 py-3 text-xs text-muted">
        {data.spend !== null && data.cap !== null && (
          <p>
            Spent today <b className="font-medium text-ink">{money(data.spend)}</b> of {money(data.cap)}
          </p>
        )}
        <div className="flex items-center justify-between gap-2">
          <span aria-hidden>Developer tools</span>
          <button
            type="button"
            role="switch"
            aria-checked={developer}
            aria-label="Developer tools"
            onClick={() => setDeveloper(!developer)}
            className={cn(
              "relative h-5 w-9 shrink-0 rounded-full border transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal/40",
              developer ? "border-teal bg-teal" : "border-muted/60 bg-white",
            )}
          >
            <span
              aria-hidden
              className={cn(
                "absolute top-0.5 h-3.5 w-3.5 rounded-full transition-all",
                developer ? "left-[1.1rem] bg-white" : "left-0.5 bg-muted",
              )}
            />
          </button>
        </div>
        {identity && (
          <p className="truncate" title={identity}>
            {identity}
          </p>
        )}
      </div>
    </div>
  );
}
