// One nav list for the whole console. `spa: true` pages are client-routed;
// the rest are still server-rendered and reached by a normal page load until
// their port lands (docs/plans/2026-10-30-1400-feat-console-react-unification-plan.md).
// Labels and hrefs match services/opsview/registry.py so the two navs agree.
import type { IconName } from "@/components/icons/paths";

export interface NavItem {
  label: string;
  icon: IconName;
  href: string;
  spa: boolean;
  /** Rendered by the server only (no SPA route); the static demo cannot serve it, so the demo build hides it. */
  server?: boolean;
}

const ALL: NavItem[] = [
  { label: "Chat", icon: "chat", href: "/", spa: true },
  { label: "Work", icon: "work", href: "/work", spa: false, server: true },
  { label: "Overview", icon: "overview", href: "/overview", spa: true },
  { label: "Goals", icon: "goals", href: "/goals", spa: true },
  { label: "Automations", icon: "automations", href: "/automations", spa: true },
  { label: "Skills", icon: "skills", href: "/skills", spa: true },
  { label: "Sandboxes", icon: "sandboxes", href: "/sandboxes", spa: true },
  { label: "Devices", icon: "devices", href: "/devices", spa: true },
  { label: "Runs", icon: "runs", href: "/runs", spa: true },
  { label: "Approvals", icon: "approvals", href: "/approvals", spa: true },
  { label: "Router", icon: "router", href: "/router", spa: true },
  { label: "Audit", icon: "audit", href: "/audit", spa: true },
  { label: "Workspace", icon: "workspace", href: "/workspace", spa: false, server: true },
  { label: "Settings", icon: "settings", href: "/settings", spa: true },
];

/** The console nav. Compile-time filter: the demo build drops the server-rendered pages. */
export const navFor = (demo: boolean): NavItem[] => (demo ? ALL.filter((n) => !n.server) : ALL);
export const NAV: NavItem[] = navFor(__OC_DEMO__);
