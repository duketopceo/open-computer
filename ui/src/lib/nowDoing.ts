// The one-line "now doing" summary of a live run, from the run detail every chat already polls.
// Pure, so the wording is tested without a server; the click-through shows the full stream.
import type { RunDetail } from "./api";
import { waitingText } from "./friendly";

export interface NowDoing {
  /** plain words: what the run is doing right now */
  text: string;
  /** when the current activity started (epoch seconds), for the live clock */
  since?: number;
  /** the owner is the blocker, not the run */
  needsYou?: boolean;
}

const live = (state: string) => state === "RUNNING" || state === "LEASED";

const TERMINAL = new Set(["DONE", "FAILED"]);

/** null when nothing is in progress: the run finished, failed, was cancelled, or waits on a plan decision shown elsewhere.
 *  Not `run.ended`: the API sets it to the latest audit row's time, so a live run has one too. A run is over when every
 *  declared step is terminal. */
export function nowDoing(run: RunDetail): NowDoing | null {
  if (run.error) return null;
  if (run.plan?.state === "planned" || run.plan?.state === "cancelled") return null;
  if (run.effort?.paused) return null;
  const steps = run.subtasks.filter((s) => !s.skipped);
  const title = (s: { title?: string; subtask: string }) => s.title || s.subtask;
  const awaiting = steps.find((s) => s.state === "AWAITING-APPROVAL");
  if (awaiting) return { text: `Waiting for your approval: ${title(awaiting)}`, needsYou: true };
  const running = steps.filter((s) => live(s.state));
  if (running.length) {
    const more = running.length > 1 ? ` (+${running.length - 1} more)` : "";
    return { text: `${title(running[0])}${more}`, since: running[0].started_ts ?? undefined };
  }
  const backingOff = steps.find((s) => s.state === "QUEUED" && s.waiting_on);
  if (backingOff?.waiting_on) {
    const w = backingOff.waiting_on;
    return { text: `${title(backingOff)}: ${waitingText(w.kind, w.attempt, w.max).replace(/^Waiting/, "waiting")}`, since: w.since };
  }
  if (run.subtasks.length === 0) return run.declared > 0 ? null : { text: "Planning the steps", since: run.started };
  if (run.subtasks.length >= run.declared && run.subtasks.every((s) => s.skipped || TERMINAL.has(s.state))) return null;
  const next = steps.find((s) => s.state === "QUEUED");
  return next ? { text: `Up next: ${title(next)}` } : null;
}
