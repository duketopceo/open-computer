import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  ApiError,
  errorMessage,
  getRun,
  listRuns,
  session,
  submitGoal,
  type RunDetail,
  type RunSummary,
  type Session,
} from "@/lib/api";
import { Composer } from "@/components/Composer";
import { RunView } from "@/components/RunView";
import { Sidebar } from "@/components/Sidebar";
import { Button } from "@/components/primitives";

const STARTERS = [
  { title: "Build an ASCII editor", body: "Build an interactive ASCII editor with drawing tools and undo/redo." },
  { title: "Map a dataset", body: "Create an interactive map of the geographic density of a public dataset." },
  { title: "Write a haiku", body: "Write a haiku about receipts and render it as a text file." },
];

// What the banner says about the backend link — independent of any one action.
type Link = "ok" | "offline" | "auth";

export default function App() {
  const [sess, setSess] = useState<Session | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [run, setRun] = useState<RunDetail | null>(null);
  const [runError, setRunError] = useState("");
  const [confirm, setConfirm] = useState<{ goal: string; cap: number; error?: string } | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [starter, setStarter] = useState<string | undefined>();
  const [draft, setDraft] = useState<string | undefined>();
  const [error, setError] = useState("");
  const [link, setLink] = useState<Link>("ok");

  // Responses for a run the user has navigated away from must be dropped.
  // Synced at commit (not render): concurrent React may abandon a render.
  const selectedRef = useRef<string | null>(null);
  useLayoutEffect(() => {
    selectedRef.current = selected;
  }, [selected]);

  // A success only clears "offline"; an expired session needs a reload.
  const noteSuccess = useCallback(() => setLink((l) => (l === "auth" ? l : "ok")), []);

  const noteFailure = useCallback((e: unknown) => {
    if (e instanceof ApiError && e.kind === "auth") setLink("auth");
    else if (e instanceof ApiError && e.kind === "network") setLink((l) => (l === "auth" ? l : "offline"));
  }, []);

  const refreshRuns = useCallback(
    () =>
      listRuns().then((r) => {
        setRuns(r);
        noteSuccess();
        return r;
      }),
    [noteSuccess],
  );

  useEffect(() => {
    session()
      .then(setSess)
      .catch(noteFailure);
    refreshRuns()
      .then((r) => {
        if (r.length && selectedRef.current === null) setSelected(r[0].run);
      })
      .catch((e) => {
        noteFailure(e);
        setError(errorMessage(e, "Could not load runs."));
      });
  }, [noteFailure, refreshRuns]);

  const fetchRun = useCallback(
    (id: string) =>
      getRun(id)
        .then((r) => {
          if (selectedRef.current !== id) return; // stale: selection moved on
          setRun(r);
          setRunError("");
          noteSuccess();
        })
        .catch((e) => {
          if (selectedRef.current !== id) return;
          noteFailure(e);
          if (e instanceof ApiError && e.status === 404) setRunError("This run no longer exists.");
          else if (!(e instanceof ApiError && e.kind === "network")) setRunError(errorMessage(e, "Could not load run."));
        }),
    [noteFailure, noteSuccess],
  );

  useEffect(() => {
    setRun(null); // never show the previous run under a new selection
    setRunError("");
    if (selected) fetchRun(selected);
  }, [selected, fetchRun]);

  // Poll: runs rail + selected run. Fast while a run is active, slow when idle;
  // backs off while the link is down, stops once the session is gone.
  const active = !!run?.subtasks.some((s) => s.state === "RUNNING" || s.state === "LEASED" || s.state === "QUEUED");
  useEffect(() => {
    if (link === "auth") return;
    const ms = link === "offline" ? 10000 : active ? 2500 : 8000;
    const id = setInterval(() => {
      refreshRuns().catch(noteFailure);
      if (selected) fetchRun(selected);
    }, ms);
    return () => clearInterval(id);
  }, [selected, active, link, refreshRuns, fetchRun, noteFailure]);

  function onRun(goal: string) {
    if (submitting) return;
    setError("");
    setSubmitting(true);
    submitGoal(goal, false)
      .then((r) => {
        if (r.run) select(r.run);
        else if (r.needs_confirm) setConfirm({ goal, cap: r.cap ?? 0 });
        else setError("The server didn't start a run or ask to confirm. Try again.");
      })
      .catch((e) => {
        noteFailure(e);
        setDraft(goal); // keep what they typed
        setError(errorMessage(e, "Could not submit."));
      })
      .finally(() => setSubmitting(false));
  }

  function confirmRun() {
    if (!confirm || submitting) return;
    const goal = confirm.goal;
    setSubmitting(true);
    setConfirm({ ...confirm, error: undefined });
    submitGoal(goal, true)
      .then((r) => {
        if (!r.run) throw new Error("The server accepted the goal but returned no run id.");
        setConfirm(null); // only once it actually started
        select(r.run);
        refreshRuns().catch(noteFailure);
      })
      .catch((e) => {
        noteFailure(e);
        setConfirm((c) => (c ? { ...c, error: errorMessage(e, "Could not start the run.") } : c));
      })
      .finally(() => setSubmitting(false));
  }

  function cancelConfirm() {
    if (submitting) return;
    if (confirm) setDraft(confirm.goal);
    setConfirm(null);
  }

  function select(id: string) {
    setSelected(id);
    setStarter(undefined);
    setDraft(undefined);
  }

  return (
    <div className="flex h-full">
      <Sidebar
        runs={runs}
        selected={selected}
        identity={sess?.identity ?? ""}
        onSelect={(id) => {
          if (submitting) return;
          setConfirm(null);
          setError("");
          setSelected(id || null);
        }}
      />

      <main className="relative flex min-w-0 flex-1 flex-col overflow-y-auto">
        {link !== "ok" && <LinkBanner link={link} />}
        <div className="mx-auto flex w-full max-w-2xl flex-1 flex-col justify-center px-6 py-10">
          {error && (
            <p className="mb-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
              {error}
            </p>
          )}

          {confirm ? (
            <ConfirmCard
              goal={confirm.goal}
              cap={confirm.cap}
              error={confirm.error}
              busy={submitting}
              onRun={confirmRun}
              onCancel={cancelConfirm}
            />
          ) : run ? (
            <RunView run={run} />
          ) : selected && runError ? (
            <div className="flex flex-col items-center gap-3 text-center">
              <p className="text-sm text-red-700" role="alert">
                {runError}
              </p>
              <div className="flex gap-2">
                <Button variant="subtle" onClick={() => fetchRun(selected)}>
                  Retry
                </Button>
                <Button variant="ghost" onClick={() => setSelected(null)}>
                  New goal
                </Button>
              </div>
            </div>
          ) : selected ? (
            <div className="flex flex-col items-center gap-3 text-muted" role="status">
              <span className="h-4 w-4 animate-spin rounded-full border-2 border-muted/30 border-t-teal" />
              <p className="text-sm">Loading run…</p>
            </div>
          ) : (
            <div className="flex flex-col items-center">
              <h1 className="font-display text-3xl text-ink">What should the computer do?</h1>
              <p className="mt-2 mb-8 max-w-md text-center text-sm text-muted">
                Describe an outcome. It decomposes, routes, places and runs each step — here you
                watch the machinery, not just the answer.
              </p>
              <Composer onRun={onRun} starter={starter ?? draft} busy={submitting} disabled={link === "auth"} />
              <div className="mt-6 grid w-full grid-cols-1 gap-3 sm:grid-cols-3">
                {STARTERS.map((s) => (
                  <button
                    key={s.title}
                    onClick={() => setStarter(s.body)}
                    className="rounded-xl border border-line bg-white p-3 text-left shadow-card transition-transform hover:-translate-y-0.5"
                  >
                    <p className="text-sm font-medium text-ink">{s.title}</p>
                    <p className="mt-1 text-xs leading-relaxed text-muted">{s.body}</p>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}

function LinkBanner({ link }: { link: Exclude<Link, "ok"> }) {
  return (
    <div
      role="status"
      className="sticky top-0 z-10 flex items-center justify-center gap-3 border-b border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-900"
    >
      {link === "auth" ? "Your session expired." : "Connection lost — retrying…"}
      <Button variant="subtle" className="h-7" onClick={() => window.location.reload()}>
        {link === "auth" ? "Sign in again" : "Reload"}
      </Button>
    </div>
  );
}

function ConfirmCard({
  goal,
  cap,
  error,
  busy,
  onRun,
  onCancel,
}: {
  goal: string;
  cap: number;
  error?: string;
  busy: boolean;
  onRun: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="mx-auto w-full max-w-xl animate-fade-up rounded-2xl border border-line bg-white p-5 shadow-card">
      <h2 className="font-display text-xl text-ink">Run this?</h2>
      <p className="mt-2 text-sm text-ink/90">{goal}</p>
      <p className="mt-3 text-xs leading-relaxed text-muted">
        A planner call decomposes the goal; subtasks placed cloud-* run against OpenRouter and are
        metered against the ${cap}/day cap. Anything flagged private stays local or waits for
        approval.
      </p>
      {error && (
        <p className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
          {error}
        </p>
      )}
      <div className="mt-4 flex items-center gap-2">
        <Button variant="primary" onClick={onRun} disabled={busy} aria-busy={busy}>
          {busy ? "Starting…" : error ? "Try again" : "Run it"}
        </Button>
        <Button variant="subtle" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
      </div>
    </div>
  );
}
