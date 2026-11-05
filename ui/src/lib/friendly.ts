/** Human labels for internal names — users never see raw enums, ids, or kinds. */

const ERROR_KINDS: Record<string, string> = {
  CancelledError: "Cancelled",
  DependencyFailed: "Skipped",
  NoPlacement: "Couldn't be placed",
  CommandExitException: "Step failed",
  SandboxWorkerUnavailable: "Sandbox unavailable",
  SandboxUnavailable: "Sandbox unavailable",
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

const WAITING: Record<string, string> = {
  sandbox_boot: "sandbox",
};

/** "Waiting for sandbox — attempt 2 of 4" for a requeued step. */
export function waitingText(kind: string, attempt: number, max: number): string {
  return `Waiting for ${WAITING[kind] ?? kind.replace(/_/g, " ")} — retrying, attempt ${attempt + 1} of ${max}`;
}

/** "Skipped — step 2 failed": names the dependency (by its position in the run) whose failure stopped this step. */
export function skippedWhy(error: string | undefined, steps: { subtask: string }[]): string {
  const id = /dep '([^']+)' failed/.exec(error ?? "")?.[1];
  const at = id ? steps.findIndex((x) => x.subtask === id) : -1;
  return at >= 0 ? `Skipped — step ${at + 1} failed` : "Skipped — an earlier step failed";
}
