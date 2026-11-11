import { useCallback, useEffect, useState } from "react";
import { getOverview, listAgentJobs, listRuns, type RunSummary } from "@/lib/api";

const POLL_MS = 15000;

export interface ShellData {
  runs: RunSummary[];
  /** Everything waiting on the person: queued approvals plus agent-job approvals. */
  pending: number;
  spend: number | null;
  cap: number | null;
}

/** What the sidebar shows on every page. Failures are silent here: each page owns its own error
 *  state, and a sidebar that nags makes every page look broken. `ownRuns` lets the home page
 *  pass the list it already polls instead of fetching it twice. */
export function useShellData(ownRuns?: RunSummary[]): ShellData {
  const ownsRuns = ownRuns !== undefined;
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [pending, setPending] = useState(0);
  const [money, setMoney] = useState<{ spend: number; cap: number } | null>(null);

  const load = useCallback(() => {
    if (!ownsRuns) listRuns().then(setRuns).catch(() => {});
    const queue = getOverview()
      .then((o) => {
        setMoney({ spend: o.spend, cap: o.cap });
        return o.pending_approvals.length;
      })
      .catch(() => null);
    const jobs = listAgentJobs()
      .then((j) => j.reduce((n, x) => n + (x.pending || 0), 0))
      .catch(() => null);
    Promise.all([queue, jobs]).then(([a, b]) => {
      if (a !== null || b !== null) setPending((a ?? 0) + (b ?? 0));
    });
  }, [ownsRuns]);

  useEffect(() => {
    load();
    const id = setInterval(load, POLL_MS);
    return () => clearInterval(id);
  }, [load]);

  return { runs: ownRuns ?? runs, pending, spend: money?.spend ?? null, cap: money?.cap ?? null };
}
