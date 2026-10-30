import { Link, useRouter } from "@/lib/router";
import type { NavItem } from "@/lib/nav";
import { cn } from "@/lib/cn";

const ITEM = "flex items-center rounded-lg px-2.5 py-1.5 text-sm transition-colors";

export function NavList({ items, label = "Console" }: { items: NavItem[]; label?: string }) {
  const { path } = useRouter();
  return (
    <nav aria-label={label}>
      <ul>
        {items.map((it) => {
          const here = path.replace(/\/+$/, "") || "/";
          // sandbox detail (/sandboxes/<id>) keeps its section highlighted
          const active =
            it.spa && (here === it.href || (it.href !== "/" && here.startsWith(it.href + "/"))) ? "page" : undefined;
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
