import { Link, useRouter } from "@/lib/router";
import type { NavItem } from "@/lib/nav";
import { cn } from "@/lib/cn";

const ITEM = "flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal/40";

function Icon({ name }: { name?: NavItem["icon"] }) {
  const common = { width: 16, height: 16, viewBox: "0 0 24 24", fill: "none", "aria-hidden": true } as const;
  const s = { stroke: "currentColor", strokeWidth: 1.7, strokeLinecap: "round", strokeLinejoin: "round" } as const;
  if (name === "new") return <svg {...common}><path d="M12 5v14M5 12h14" {...s} /></svg>;
  if (name === "tasks") return <svg {...common}><path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01" {...s} /></svg>;
  if (name === "needs") return <svg {...common}><path d="M6 9a6 6 0 1 1 12 0c0 6 2 7 2 7H4s2-1 2-7M10 20a2 2 0 0 0 4 0" {...s} /></svg>;
  if (name === "clock") return <svg {...common}><circle cx="12" cy="12" r="8" {...s} /><path d="M12 8v4l3 2" {...s} /></svg>;
  return null;
}

export function NavList({
  items,
  label = "Console",
  badges = {},
  onNew,
  viewingRun,
}: {
  items: NavItem[];
  label?: string;
  /** href -> count shown as a pill (the screen-reader text says what it counts) */
  badges?: Record<string, { count: number; what: string }>;
  /** Called when "New" is clicked, so the home page can drop its current task. */
  onNew?: () => void;
  /** The home page is showing a task, so "New" is not the current page. */
  viewingRun?: boolean;
}) {
  const { path } = useRouter();
  const here = path.replace(/\/+$/, "") || "/";
  return (
    <nav aria-label={label}>
      <ul>
        {items.map((it) => {
          // a page one segment below the section (/sandboxes/<id>) keeps its section highlighted
          const below = it.href !== "/" && here.startsWith(it.href + "/") && !here.slice(it.href.length + 1).includes("/");
          const active = it.spa && (here === it.href || below) && !(it.href === "/" && viewingRun) ? "page" : undefined;
          const cls = cn(ITEM, active ? "bg-teal-soft font-medium text-teal" : "text-ink/80 hover:bg-black/5 hover:text-ink");
          const badge = badges[it.href];
          const inner = (
            <>
              <Icon name={it.icon} />
              {it.label}
              {badge && badge.count > 0 && (
                <>
                  <span aria-hidden className="ml-auto rounded-full bg-teal px-1.5 text-[11px] font-semibold leading-5 text-white">
                    {badge.count}
                  </span>
                  <span className="sr-only">
                    , {badge.count} {badge.what}
                  </span>
                </>
              )}
            </>
          );
          return (
            <li key={it.href}>
              {it.spa ? (
                <Link href={it.href} aria-current={active} className={cls} onClick={it.href === "/" ? onNew : undefined}>
                  {inner}
                </Link>
              ) : (
                <a href={it.href} className={cls}>
                  {inner}
                </a>
              )}
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
