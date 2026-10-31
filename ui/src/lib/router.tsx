// Tiny history router for the console SPA (KTD3 in the unification plan): a
// path-pattern table, <Link>, navigate(). No dependency, no nested layouts.
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type AnchorHTMLAttributes,
  type MouseEvent,
  type ReactNode,
} from "react";

export interface Match<R> {
  route: R;
  params: Record<string, string>;
}

function split(path: string): string[] {
  return path.split("/").filter(Boolean);
}

/** Match `path` against `{ path: "/sandboxes/:id" }` rows. Trailing slashes are ignored. */
export function matchRoute<R extends { path: string }>(path: string, table: R[]): Match<R> | null {
  const parts = split(path);
  for (const route of table) {
    const pat = split(route.path);
    if (pat.length !== parts.length) continue;
    const params: Record<string, string> = {};
    let ok = true;
    for (let i = 0; i < pat.length; i++) {
      if (pat[i].startsWith(":")) {
        try {
          params[pat[i].slice(1)] = decodeURIComponent(parts[i]);
        } catch {
          ok = false; // malformed %-escape: not a match
          break;
        }
      } else if (pat[i] !== parts[i]) {
        ok = false;
        break;
      }
    }
    if (ok) return { route, params };
  }
  return null;
}

interface Loc {
  path: string;
  search: string;
}

interface RouterValue extends Loc {
  /** How many client-side navigations (links, back/forward) happened in this document. */
  navs: number;
  navigate: (to: string, opts?: { replace?: boolean }) => void;
}

const RouterContext = createContext<RouterValue | null>(null);

function here(): Loc {
  return { path: window.location.pathname, search: window.location.search };
}

export function RouterProvider({ children }: { children: ReactNode }) {
  const [loc, setLoc] = useState<Loc>(here);
  const [navs, setNavs] = useState(0);
  useEffect(() => {
    const onPop = () => {
      setLoc(here());
      setNavs((n) => n + 1);
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  const navigate = useCallback((to: string, opts?: { replace?: boolean }) => {
    // Going to where we already are would stack a duplicate history entry.
    if (new URL(to, window.location.href).href === window.location.href) return;
    if (opts?.replace) window.history.replaceState(null, "", to);
    else window.history.pushState(null, "", to);
    setLoc(here());
    setNavs((n) => n + 1);
  }, []);
  const value = useMemo(() => ({ ...loc, navs, navigate }), [loc, navs, navigate]);
  return <RouterContext.Provider value={value}>{children}</RouterContext.Provider>;
}

export function useRouter(): RouterValue {
  const v = useContext(RouterContext);
  if (!v) throw new Error("useRouter must be used inside <RouterProvider>");
  return v;
}

/** A real <a href>; only plain left clicks are handled client-side. */
export function Link({
  href,
  onClick,
  ...rest
}: AnchorHTMLAttributes<HTMLAnchorElement> & { href: string }) {
  const { navigate } = useRouter();
  const handle = (e: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(e);
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if (rest.target && rest.target !== "_self") return;
    e.preventDefault();
    navigate(href);
  };
  return <a href={href} onClick={handle} {...rest} />;
}
