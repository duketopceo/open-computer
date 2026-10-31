import { Link, useRouter } from "@/lib/router";
import type { NavItem } from "@/lib/nav";
import { cn } from "@/lib/cn";

const ITEM = "flex items-center rounded-lg px-2.5 py-1.5 text-sm transition-colors";

export function NavList({ items, label = "Console" }: { items: NavItem[]; label?: string }) {
  const { path } = useRouter();
  const here = path.replace(/\/+$/, "") || "/";
  return (
    <nav aria-label={label}>
      <ul>
        {items.map((it) => {
          // sandbox detail (/sandboxes/<id>) keeps its section highlighted
          // only one segment below the section (/sandboxes/<id>); deeper paths are not a declared route
          const below = it.href !== "/" && here.startsWith(it.href + "/") && !here.slice(it.href.length + 1).includes("/");
          const active = it.spa && (here === it.href || below) ? "page" : undefined;
          const cls = cn(ITEM, active ? "bg-teal-soft font-medium text-teal" : "text-muted hover:bg-black/5 hover:text-ink");
          return (
            <li key={it.href}>
              {it.spa ? (
                <Link href={it.href} aria-current={active} className={cls}>
                  {it.label}
                </Link>
              ) : (
                <a href={it.href} className={cls}>
                  {it.label}
                </a>
              )}
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
