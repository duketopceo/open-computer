import { useEffect, useRef, useState, type ReactNode } from "react";
import { NAV } from "@/lib/nav";
import { session } from "@/lib/api";
import { useRouter } from "@/lib/router";
import { NavList } from "./NavList";

export function Brand() {
  return (
    <span className="flex items-center gap-2">
      <span className="grid h-6 w-6 place-items-center rounded-md bg-teal text-white">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden>
          <path d="M4 5h16v12H4z M8 9h8M8 12h5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </span>
      <span className="text-sm font-semibold text-ink">Open Computer</span>
    </span>
  );
}

// True after the first page render of this document: later renders are client-side navigations.
let booted = false;

/** Chrome for every SPA page except `/` (which keeps its own run-focused layout). */
export function PageShell({ title, sub, children }: { title: string; sub?: string; children: ReactNode }) {
  const { path, search } = useRouter();
  const [identity, setIdentity] = useState("");
  const [open, setOpen] = useState(false);
  const main = useRef<HTMLElement>(null);

  useEffect(() => {
    session()
      .then((s) => setIdentity(s.identity))
      .catch(() => {});
  }, []);

  // Client-side navigation moves focus to the page and retitles it; the first
  // render is a real page load, where the browser already did both.
  useEffect(() => {
    document.title = `${title} · open-computer`;
    setOpen(false);
    if (!booted) {
      booted = true;
      return;
    }
    main.current?.focus();
  }, [path, search, title]);

  return (
    <div className="flex h-full flex-col md:flex-row">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-20 focus:rounded-lg focus:bg-white focus:px-3 focus:py-2 focus:text-sm focus:shadow-card"
      >
        Skip to content
      </a>
      <aside className="hidden h-full w-60 shrink-0 flex-col border-r border-line bg-white/60 md:flex">
        <div className="px-4 py-4">
          <Brand />
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-2">
          <NavList items={NAV} />
        </div>
        {identity && (
          <p className="truncate border-t border-line px-4 py-3 text-[11px] text-muted" title={identity}>
            {identity}
          </p>
        )}
      </aside>

      <header className="flex items-center justify-between border-b border-line bg-white/60 px-4 py-3 md:hidden">
        <Brand />
        <button
          type="button"
          aria-expanded={open}
          aria-controls="mobile-nav"
          onClick={() => setOpen((o) => !o)}
          className="h-9 rounded-lg border border-line bg-white px-3 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal/40"
        >
          {open ? "Close menu" : "Menu"}
        </button>
      </header>
      {open && (
        <div id="mobile-nav" className="border-b border-line bg-white px-2 py-2 md:hidden">
          <NavList items={NAV} />
        </div>
      )}

      <main
        id="main"
        ref={main}
        tabIndex={-1}
        className="min-w-0 flex-1 overflow-y-auto px-4 py-6 focus:outline-none md:px-8 md:py-8"
      >
        <div className="mx-auto w-full max-w-5xl">
          <h1 className="font-display text-2xl text-ink">{title}</h1>
          {sub ? <p className="mt-1 mb-6 max-w-2xl text-sm text-muted">{sub}</p> : <div className="mb-6" />}
          {children}
        </div>
      </main>
    </div>
  );
}
