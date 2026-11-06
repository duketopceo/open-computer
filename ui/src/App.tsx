import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  ApiError,
  archiveRun,
  archiveThread,
  clearFinishedRuns,
  clearFinishedThreads,
  createThread,
  deleteRun,
  deleteThread,
  errorMessage,
  getRun,
  getThread,
  listAgentJobs,
  listRuns,
  listThreads,
  pollMs,
  sendMessage,
  session,
  type AgentJobSummary,
  type GoalOptions,
  type RunDetail,
  type RunSummary,
  type Session,
  type ThreadDetail,
  type ThreadSummary,
} from "@/lib/api";
import { AgentJobs } from "@/components/AgentJobs";
import { Composer } from "@/components/Composer";
import { RunView } from "@/components/RunView";
import { Timeline } from "@/components/runtime/Timeline";
import { Sidebar } from "@/components/Sidebar";
import { ThreadView } from "@/components/ThreadView";
import { Button } from "@/components/primitives";

// What the banner says about the backend link, independent of any one action.
type Link = "ok" | "offline" | "auth";

const isThread = (id: string | null) => !!id && id.startsWith("th_");
const runActive = (r: RunDetail | null | undefined) =>
  !!r?.subtasks.some((s) => s.state === "RUNNING" || s.state === "LEASED" || s.state === "QUEUED" || s.state === "AWAITING-APPROVAL");

export default function App() {
  const [sess, setSess] = useState<Session | null>(null);
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [thread, setThread] = useState<ThreadDetail | null>(null);
  const [run, setRun] = useState<RunDetail | null>(null);
  const [timeline, setTimeline] = useState(false); // #goal_x/timeline: the dev-mode event view of a run
  const [selError, setSelError] = useState("");
  const [sending, setSending] = useState(false);
  const [draft, setDraft] = useState<string | undefined>();
  const [error, setError] = useState("");
  const [link, setLink] = useState<Link>("ok");
  const [view, setView] = useState<"chat" | "agents">("chat");
  const [jobs, setJobs] = useState<AgentJobSummary[] | null>(null);
  const [jobsError, setJobsError] = useState("");

  // Responses for something the user has navigated away from must be dropped.
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
      // The chat list owns the link signal: this one may fail without flapping the banner, and never clears it.
      listRuns(true, true).then((r) => {
        setRuns(r);
        return r;
      }),
    [],
  );

  const refreshThreads = useCallback(
    () =>
      listThreads().then((t) => {
        setThreads(t);
        noteSuccess();
        return t;
      }),
    [noteSuccess],
  );

  // Never rejects: failures land in jobsError (inline retry) and the link banner. A success here
  // must not clear "offline": the lists poll owns that signal.
  const refreshJobs = useCallback(
    () =>
      listAgentJobs()
        .then((j) => {
          setJobs(j);
          setJobsError("");
        })
        .catch((e) => {
          noteFailure(e);
          if (!(e instanceof ApiError && e.kind === "auth")) setJobsError(errorMessage(e, "Could not load agent jobs."));
        }),
    [noteFailure],
  );

  useEffect(() => {
    refreshJobs();
  }, [refreshJobs]);

  const jobsActive = !!jobs?.some((j) => j.status === "running" || j.awaiting_approval);
  useEffect(() => {
    if (link === "auth") return;
    const id = setInterval(refreshJobs, pollMs(link, jobsActive));
    return () => clearInterval(id);
  }, [link, jobsActive, refreshJobs]);

  useEffect(() => {
    const fromHash = () => {
      const [id, sub] = location.hash.replace(/^#/, "").split("/");
      if (id) {
        setSelected(id);
        setTimeline(sub === "timeline");
      }
    };
    fromHash();
    addEventListener("hashchange", fromHash);
    return () => removeEventListener("hashchange", fromHash);
  }, []);

  useEffect(() => {
    session().then(setSess).catch(noteFailure);
    refreshThreads().catch((e) => {
      noteFailure(e);
      setError(errorMessage(e, "Could not load chats."));
    });
    refreshRuns().catch(noteFailure);
  }, [noteFailure, refreshThreads, refreshRuns]);

  const fetchSelected = useCallback(
    (id: string) => {
      const stale = () => selectedRef.current !== id;
      const fail = (e: unknown) => {
        if (stale()) return;
        noteFailure(e);
        if (e instanceof ApiError && e.status === 404) setSelError(isThread(id) ? "This chat no longer exists." : "This run no longer exists.");
        else if (!(e instanceof ApiError && e.kind === "network")) setSelError(errorMessage(e, "Could not load."));
      };
      const ok = () => {
        setSelError("");
        noteSuccess();
      };
      if (isThread(id)) {
        return getThread(id)
          .then((t) => {
            if (stale()) return;
            setThread(t);
            ok();
          })
          .catch(fail);
      }
      return getRun(id)
        .then((r) => {
          if (stale()) return;
          setRun(r);
          ok();
        })
        .catch(fail);
    },
    [noteFailure, noteSuccess],
  );

  useEffect(() => {
    setThread(null); // never show the previous conversation under a new selection
    setRun(null);
    setSelError("");
    if (selected) fetchSelected(selected);
  }, [selected, fetchSelected]);

  // Poll the rail and the open item. Fast while something is working, slow when idle;
  // backs off while the link is down, stops once the session is gone.
  const active = !!thread?.working || Object.values(thread?.runs ?? {}).some(runActive) || runActive(run);
  useEffect(() => {
    if (link === "auth") return;
    const id = setInterval(() => {
      refreshThreads().catch(noteFailure);
      refreshRuns().catch(noteFailure);
      if (selected) fetchSelected(selected);
    }, pollMs(link, active));
    return () => clearInterval(id);
  }, [selected, active, link, refreshThreads, refreshRuns, fetchSelected, noteFailure]);

  function select(id: string | null) {
    setView("chat");
    setTimeline(false);
    setError("");
    setDraft(undefined);
    setSelected(id || null);
    history.replaceState(null, "", id ? `#${id}` : location.pathname);
  }

  // The message appears at once; the server answers in the background and the poll fills the reply in.
  function onSend(text: string, opts: GoalOptions) {
    if (sending) return;
    setError("");
    setDraft(undefined);
    setSending(true);
    const failed = (e: unknown) => {
      noteFailure(e);
      setDraft(text); // keep what they typed
      setError(errorMessage(e, "Could not send."));
      if (selected) fetchSelected(selected);
    };
    if (isThread(selected) && thread) {
      setThread({ ...thread, working: true, messages: [...thread.messages, { role: "user", text, ts: Date.now() / 1000 }] });
      sendMessage(thread.id, text)
        .then(() => fetchSelected(thread.id))
        .catch(failed)
        .finally(() => setSending(false));
    } else {
      createThread(text, opts)
        .then((r) => {
          select(r.thread);
          refreshThreads().catch(noteFailure);
        })
        .catch(failed)
        .finally(() => setSending(false));
    }
  }

  // Cleanup: refresh the rail and, if the open item is gone, drop back to a new chat.
  const cleanup = <T,>(call: () => Promise<T>, fail: string, goneIds: () => string[]) => {
    setError("");
    const gone = goneIds();
    call()
      .then(() => {
        if (selected && gone.includes(selected)) select(null);
        refreshThreads().catch(noteFailure);
        refreshRuns().catch(noteFailure);
      })
      .catch((e) => {
        noteFailure(e);
        setError(errorMessage(e, fail));
      });
  };

  const chats = {
    onDelete: (id: string) => cleanup(() => deleteThread(id), "Could not delete the chat.", () => [id]),
    onArchive: (id: string, a: boolean) => cleanup(() => archiveThread(id, a), "Could not archive the chat.", () => (a ? [id] : [])),
    onClear: () =>
      cleanup(clearFinishedThreads, "Could not clear finished chats.", () => threads.filter((t) => t.finished && !t.archived).map((t) => t.id)),
  };
  const earlier = {
    onDelete: (id: string) => cleanup(() => deleteRun(id), "Could not delete the run.", () => [id]),
    onArchive: (id: string, a: boolean) => cleanup(() => archiveRun(id, a), "Could not archive the run.", () => (a ? [id] : [])),
    onClear: () =>
      cleanup(clearFinishedRuns, "Could not clear finished runs.", () => runs.filter((r) => r.finished && !r.archived).map((r) => r.run)),
  };

  const gone = selected && selError;
  return (
    <div className="flex h-full">
      <Sidebar
        threads={threads}
        runs={runs}
        selected={selected}
        identity={sess?.identity ?? ""}
        view={view}
        onAgents={() => setView("agents")}
        pending={(jobs ?? []).reduce((n, j) => n + (j.pending || 0), 0)}
        onSelect={(id) => {
          if (!sending) select(id || null);
        }}
        chats={chats}
        earlier={earlier}
      />

      <main className="relative flex min-w-0 flex-1 flex-col overflow-y-auto">
        {link !== "ok" && <LinkBanner link={link} />}
        {view === "agents" ? (
          <AgentJobs jobs={jobs} jobsError={jobsError} link={link} onRefresh={refreshJobs} onOk={noteSuccess} onFail={noteFailure} />
        ) : selected && timeline && !isThread(selected) ? (
          <div className="mx-auto w-full max-w-3xl px-6 py-10">
            <a
              href={`#${selected}`}
              className="mb-4 inline-block text-xs text-muted hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              ← Back to the run
            </a>
            <Timeline key={selected} runId={selected} goal={run?.goal} />
          </div>
        ) : gone ? (
          <div className="m-auto flex flex-col items-center gap-3 px-6 text-center">
            <p className="text-sm text-urgent" role="alert">
              {selError}
            </p>
            <div className="flex gap-2">
              <Button variant="subtle" onClick={() => selected && fetchSelected(selected)}>
                Retry
              </Button>
              <Button variant="ghost" onClick={() => select(null)}>
                New chat
              </Button>
            </div>
          </div>
        ) : selected && isThread(selected) && thread ? (
          <ThreadView thread={thread} onSend={onSend} busy={sending} disabled={link !== "ok"} error={error} />
        ) : selected && !isThread(selected) && run ? (
          <div className="mx-auto w-full max-w-3xl px-6 py-10">
            <RunView run={run} />
          </div>
        ) : selected ? (
          <div className="m-auto flex flex-col items-center gap-3 text-muted" role="status">
            <span className="h-4 w-4 animate-spin rounded-full border-2 border-line border-t-accent" />
            <p className="text-sm">Loading…</p>
          </div>
        ) : (
          <div className="mx-auto flex w-full max-w-2xl flex-1 flex-col justify-center px-6 py-10">
            {error && (
              <p className="mb-4 rounded-lg border border-urgent bg-urgent-soft px-3 py-2 text-sm text-urgent" role="alert">
                {error}
              </p>
            )}
            <h1 className="mb-6 text-center font-display text-3xl text-ink">What are we working on?</h1>
            <Composer
              onSend={onSend}
              starter={draft}
              busy={sending}
              disabled={link !== "ok"}
              showEffort
              autoFocus
            />
          </div>
        )}
      </main>
    </div>
  );
}

function LinkBanner({ link }: { link: Exclude<Link, "ok"> }) {
  return (
    <div
      role="status"
      className="sticky top-0 z-10 flex items-center justify-center gap-3 border-b border-attention bg-attention-soft px-4 py-2 text-sm text-attention"
    >
      {link === "auth" ? "Your session expired." : "Connection lost — retrying…"}
      <Button variant="subtle" className="h-7" onClick={() => window.location.reload()}>
        {link === "auth" ? "Sign in again" : "Reload"}
      </Button>
    </div>
  );
}
