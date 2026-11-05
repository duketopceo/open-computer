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


const ERROR_SENTENCES: Record<string, string> = {
  CommandExitException: "The step's program exited with an error.",
  SandboxWorkerUnavailable: "The sandbox could not be started.",
  sandbox_boot: "The sandbox did not start.",
  DependencyFailed: "A step this one depends on failed.",
  BudgetHalt: "The run reached its budget.",
};

/** One plain sentence for an error code; the raw message stays under "Details". */
export function plainError(code?: string): string {
  if (code && ERROR_SENTENCES[code]) return ERROR_SENTENCES[code];
  return `${errorKind(code?.replace(/_/g, " "))}.`;
}

const WAITS: Record<string, string> = { sandbox_boot: "sandbox" };

/** "Waiting for sandbox — retrying, attempt 2 of 4". `attempt` counts executions already made. */
export function waitingLabel(w: { kind: string; attempt?: number; max?: number }): string {
  const what = WAITS[w.kind] ?? w.kind.replace(/_/g, " ");
  const tries = w.attempt != null ? ` — retrying, attempt ${w.attempt + 1}${w.max != null ? ` of ${w.max}` : ""}` : "";
  return `Waiting for ${what}${tries}`;
}

/** "decider_unavailable" -> "decider unavailable" */
export function words(s: string): string {
  return s.replace(/_/g, " ");
}
