// One nav for the whole console (docs/plans/2026-10-31-002-feat-general-market-ux-plan.md).
// PRIMARY is what everyone sees. DEVELOPER is operator and developer screens: shown when the
// "Developer tools" switch is on, or when the current page is one of them. It is a visibility
// switch, not access control: every route works either way.
// `spa: true` pages are client-routed; the rest are still server-rendered (full page load).
export interface NavItem {
  label: string;
  href: string;
  spa: boolean;
  icon?: "new" | "tasks" | "needs" | "clock";
}

export const PRIMARY: NavItem[] = [
  { label: "New", href: "/", spa: true, icon: "new" },
  { label: "Tasks", href: "/runs", spa: true, icon: "tasks" },
  { label: "Needs you", href: "/approvals", spa: true, icon: "needs" },
  { label: "Automations", href: "/automations", spa: true, icon: "clock" },
];

export const DEVELOPER: NavItem[] = [
  { label: "Activity summary", href: "/overview", spa: true },
  { label: "Background agents", href: "/agents", spa: true },
  { label: "Setup status", href: "/skills", spa: true },
  { label: "Workspaces", href: "/sandboxes", spa: true },
  { label: "Computers", href: "/devices", spa: true },
  { label: "Model chooser", href: "/router", spa: true },
  { label: "Activity log", href: "/audit", spa: true },
  { label: "Agent workspace", href: "/workspace", spa: false },
  { label: "Self-check", href: "/runs/new", spa: false },
];

export function isDeveloperPath(path: string): boolean {
  const here = path.replace(/\/+$/, "") || "/";
  return DEVELOPER.some((d) => here === d.href || here.startsWith(d.href + "/"));
}
