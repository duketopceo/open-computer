import { useCallback, useEffect, useState } from "react";
import {
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

export default function App() {
  const [sess, setSess] = useState<Session | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [run, setRun] = useState<RunDetail | null>(null);
  const [confirm, setConfirm] = useState<{ goal: string; cap: number } | null>(null);
  const [starter, setStarter] = useState<string | undefined>();
  const [error, setError] = useState("");

  useEffect(() => {
    session()
      .then(setSess)
      .catch(() => {});
    listRuns()
      .then((r) => {
        setRuns(r);
        if (r.length) setSelected(r[0].run);
      })
      .catch(() => {});
  }, []);

  const load = useCallback((id: string | null) => {
    if (!id) {
      setRun(null);
      return;
    }
    getRun(id).then(setRun).catch(() => {});
  }, []);

  useEffect(() => {
    load(selected);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

  // Poll: runs rail + selected run. Fast while a run is active, slow when idle.
  useEffect(() => {
    const active = run?.subtasks.some((s) => s.state === "RUNNING" || s.state === "LEASED");
    const id = setInterval(() => {
      listRuns().then(setRuns).catch(() => {});
      if (selected) getRun(selected).then(setRun).catch(() => {});
    }, active ? 2500 : 8000);
    return () => clearInterval(id);
  }, [selected, run]);

  function onRun(goal: string) {
    setError("");
    submitGoal(goal, false)
      .then((r) => {
        if (r.run) {
          select(r.run);
        } else if (r.needs_confirm) {
          setConfirm({ goal, cap: r.cap ?? 0 });
        }
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Could not submit."));
  }

  function confirmRun() {
    if (!confirm) return;
    const goal = confirm.goal;
    setConfirm(null);
    submitGoal(goal, true)
      .then((r) => {
        if (r.run) select(r.run);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Could not submit."));
  }

  function select(id: string) {
    setSelected(id);
    setStarter(undefined);
  }

  return (
    <div className="flex h-full">
      <Sidebar
        runs={runs}
        selected={selected}
        identity={sess?.identity ?? ""}
        onSelect={(id) => {
          setSelected(id || null);
        }}
      />

      <main className="relative flex min-w-0 flex-1 flex-col overflow-y-auto">
        <div className="mx-auto flex w-full max-w-2xl flex-1 flex-col justify-center px-6 py-10">
          {error && (
            <p className="mb-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
              {error}
            </p>
          )}

          {confirm ? (
            <ConfirmCard goal={confirm.goal} cap={confirm.cap} onRun={confirmRun} onCancel={() => setConfirm(null)} />
          ) : run ? (
            <RunView run={run} />
          ) : selected ? (
            <div className="flex flex-col items-center gap-3 text-muted">
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
              <Composer onRun={onRun} starter={starter} />
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

function ConfirmCard({
  goal,
  cap,
  onRun,
  onCancel,
}: {
  goal: string;
  cap: number;
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
      <div className="mt-4 flex items-center gap-2">
        <Button variant="primary" onClick={onRun}>
          Run it
        </Button>
        <Button variant="subtle" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}