// One nav list for the whole console. `spa: true` pages are client-routed;
// the rest are still server-rendered and reached by a normal page load until
// their port lands (docs/plans/2026-10-30-1400-feat-console-react-unification-plan.md).
// Labels and hrefs match services/opsview/registry.py so the two navs agree.
export interface NavItem {
  label: string;
  href: string;
  spa: boolean;
}

export const NAV: NavItem[] = [
  { label: "Computer", href: "/", spa: true },
  { label: "Work", href: "/work", spa: false },
  { label: "Overview", href: "/overview", spa: false },
  { label: "Goals", href: "/goals", spa: false },
  { label: "Automations", href: "/automations", spa: false },
  { label: "Skills", href: "/skills", spa: false },
  { label: "Sandboxes", href: "/sandboxes", spa: true },
  { label: "Devices", href: "/devices", spa: true },
  { label: "Runs", href: "/runs", spa: false },
  { label: "Approvals", href: "/approvals", spa: false },
  { label: "Router", href: "/router", spa: true },
  { label: "Audit", href: "/audit", spa: true },
  { label: "Workspace", href: "/workspace", spa: false },
];
