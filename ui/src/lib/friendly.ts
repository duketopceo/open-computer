/** Human labels for internal names — users never see raw enums, ids, or kinds. */

const ERROR_KINDS: Record<string, string> = {
  CancelledError: "Cancelled",
  DependencyFailed: "Skipped",
  NoPlacement: "Couldn't be placed",
  CommandExitException: "Step failed",
  SandboxWorkerUnavailable: "Sandbox unavailable",
  BudgetHalt: "Over budget",
};

const PLACEMENTS: Record<string, string> = {
  "cloud-OpenRouter": "Cloud model",
  "cloud-sandbox": "Sandbox",
  "local-llamacpp": "Local GPU",
  "local-ollama": "Local model",
  device: "This device",
  blocked: "Blocked",
  parked: "Waiting for device",
};

const ENGINES: Record<string, string> = {
  jev: "jev",
};

export function errorKind(kind?: string): string {
  if (!kind) return "Failed";
  return ERROR_KINDS[kind] ?? kind.replace(/([a-z])([A-Z])/g, "$1 $2");
}

export function placement(p?: string): string {
  if (!p || p === "-") return "—";
  return PLACEMENTS[p] ?? p;
}

export function engine(e?: string): string {
  if (!e || e === "-") return "";
  return ENGINES[e] ?? e;
}

/** goal_ab12cd34 -> "goal_ab12" — long ids get shortened for display */
export function shortId(id?: string): string {
  if (!id) return "";
  return id.length > 12 ? id.slice(0, 12) : id;
}
