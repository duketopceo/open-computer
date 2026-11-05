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

/** "deepseek/deepseek-v4-pro" -> "deepseek-v4-pro": the model's own name, without its vendor prefix. */
export function modelName(id?: string | null): string {
  return id ? id.slice(id.lastIndexOf("/") + 1) : "";
}

/** Why the router's first choice did not answer, in plain words ("decider unavailable (404)"); "" when it did. */
export function fallbackWhy(router?: { fallbacks?: { engine: string; status?: number | null }[] }): string {
  const fb = router?.fallbacks?.[0];
  if (!fb) return "";
  return `${fb.engine} unavailable${fb.status ? ` (${fb.status})` : ""}`;
}

/** "Skipped — step 2 failed": names the dependency (by its position in the run) whose failure stopped this step. */
export function skippedWhy(error: string | undefined, steps: { subtask: string }[]): string {
  const id = /dep '([^']+)' failed/.exec(error ?? "")?.[1];
  const at = id ? steps.findIndex((x) => x.subtask === id) : -1;
  return at >= 0 ? `Skipped — step ${at + 1} failed` : "Skipped — an earlier step failed";
}
