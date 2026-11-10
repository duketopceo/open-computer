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
}

export const NAV: NavItem[] = [
  { label: "Chat", icon: "chat", href: "/", spa: true },
  { label: "Work", icon: "work", href: "/work", spa: false },
  { label: "Overview", icon: "overview", href: "/overview", spa: false },
  { label: "Goals", icon: "goals", href: "/goals", spa: false },
  { label: "Automations", icon: "automations", href: "/automations", spa: false },
  { label: "Skills", icon: "skills", href: "/skills", spa: false },
  { label: "Sandboxes", icon: "sandboxes", href: "/sandboxes", spa: true },
  { label: "Devices", icon: "devices", href: "/devices", spa: true },
  { label: "Runs", icon: "runs", href: "/runs", spa: false },
  { label: "Approvals", icon: "approvals", href: "/approvals", spa: false },
  { label: "Router", icon: "router", href: "/router", spa: true },
  { label: "Audit", icon: "audit", href: "/audit", spa: true },
  { label: "Workspace", icon: "workspace", href: "/workspace", spa: false },
  { label: "Settings", icon: "settings", href: "/settings", spa: true },
];
