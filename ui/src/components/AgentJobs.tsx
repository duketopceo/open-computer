import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  ApiError,
  decideApproval,
  errorMessage,
  getAgentJob,
  pollMs,
  submitAgentJob,
  type AgentJobDetail,
  type AgentJobSummary,
  type Approval,
  type TrailRow,
} from "@/lib/api";
import { cost, rel } from "@/lib/format";
import { cn } from "@/lib/cn";
import { AgentStatusWord, Button, Card } from "./primitives";

type Link = "ok" | "offline" | "auth";

// Irreversible grants get a second press. egress is a kind of its own; push and
// deploy arrive as a risky request whose detail names the action.
const TWO_PRESS = new Set(["egress", "push", "deploy"]);
function needsSecondPress(a: Approval): boolean {
  if (TWO_PRESS.has(a.kind)) return true;
  return ["action", "kind", "type", "tool"].some((k) => {
    const v = a.detail?.[k];
    return typeof v === "string" && TWO_PRESS.has(v.toLowerCase());
  });
}

const budgetOf = (n: number) => `$${Number.isInteger(n) ? n : n.toFixed(2)}`;

export function AgentJobs({
  jobs,
  jobsError,
  link,
  onRefresh,
  onOk,
  onFail,
}: {
  jobs: AgentJobSummary[] | null;
  jobsError: string;
  link: Link;
  onRefresh: () => Promise<void>;
  onOk: () => void;
  onFail: (e: unknown) => void;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<AgentJobDetail | null>(null);
  const [detailError, setDetailError] = useState("");
  const [announce, setAnnounce] = useState("");

  // Responses for a job the user has navigated away from must be dropped.
  const selectedRef = useRef<string | null>(null);
  useLayoutEffect(() => {
    selectedRef.current = selected;
  }, [selected]);

  const fetchJob = useCallback(
    (id: string) =>
      getAgentJob(id)
        .then((d) => {
          if (selectedRef.current !== id) return;
          setDetail(d);
          setDetailError("");
          onOk();
        })
        .catch((e) => {
          if (selectedRef.current !== id) return;
          onFail(e);
          if (e instanceof ApiError && e.kind === "auth") return;
          setDetailError(
            e instanceof ApiError && e.status === 404 ? "This job no longer exists." : errorMessage(e, "Could not load job."),
          );
        }),
    [onOk, onFail],
  );

  useEffect(() => {
    setDetail(null); // never show the previous job under a new selection
    setDetailError("");
    if (selected) fetchJob(selected);
  }, [selected, fetchJob]);

  const live = !!jobs?.some((j) => j.status === "running" || j.awaiting_approval);
  useEffect(() => {
    if (link === "auth" || !selected) return;
    const ms = pollMs(link, live);
    const id = setInterval(() => fetchJob(selected), ms);
    return () => clearInterval(id);
  }, [selected, live, link, fetchJob]);

  // Polite live region: announce when the number of pending approvals grows.
  const prevPending = useRef<number | null>(null);
  const pending = jobs ? jobs.reduce((n, j) => n + (j.pending || 0), 0) : null;
  useEffect(() => {
    if (pending === null) return;
    if (prevPending.current !== null && pending > prevPending.current) {
      setAnnounce(`New approval pending. ${pending} awaiting your decision.`);
    }
    prevPending.current = pending;
  }, [pending]);

  const refreshAll = useCallback(() => {
    void onRefresh();
    if (selectedRef.current) void fetchJob(selectedRef.current);
  }, [onRefresh, fetchJob]);

  return (
    <div className="mx-auto w-full max-w-3xl animate-fade-up px-6 py-10">
      <div role="status" aria-live="polite" className="sr-only">
        {announce}
      </div>
      <h1 className="font-display text-2xl text-ink">Agent jobs</h1>
      <p className="mt-1 mb-6 text-sm text-muted">
        Long-horizon jobs run by the orchestrator. Risky steps wait here for your decision.
      </p>

      <SubmitForm
        disabled={link === "auth"}
        onFail={onFail}
        onSubmitted={(id) => {
          setSelected(id);
          void onRefresh();
        }}
      />

      <section aria-labelledby="aj-list" className="mt-8">
        <h2 id="aj-list" className="mb-2 text-[11px] font-medium uppercase tracking-wider text-muted">
          Jobs
        </h2>
        {jobsError ? (
          <InlineRetry message={jobsError} onRetry={() => void onRefresh()} />
        ) : jobs === null ? (
          <p className="text-sm text-muted" role="status">
            Loading jobs…
          </p>
        ) : jobs.length === 0 ? (
          <p className="rounded-2xl border border-dashed border-line px-4 py-6 text-center text-sm text-muted">
            No agent jobs yet. Submit a goal above to start one.
          </p>
        ) : (
          <ul className="divide-y divide-line rounded-2xl border border-line bg-white shadow-card">
            {jobs.map((j) => (
              <li key={j.job}>
                <button
                  onClick={() => setSelected(j.job)}
                  aria-current={selected === j.job ? "true" : undefined}
                  className={cn(
                    "flex w-full flex-col gap-1 px-4 py-3 text-left transition-colors first:rounded-t-2xl last:rounded-b-2xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal/40",
                    selected === j.job ? "bg-teal-soft" : "hover:bg-black/[.03]",
                  )}
                >
                  <span className="text-sm font-medium text-ink">{j.goal}</span>
                  <span className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
                    <AgentStatusWord status={j.status} />
                    <span className="mono text-muted">
                      {cost(j.spent)} of {budgetOf(j.budget)}
                    </span>
                    <span className="mono text-muted">{rel(j.started)}</span>
                    {j.awaiting_approval && (
                      <span className="rounded-full bg-violet-100 px-2 py-0.5 text-[11px] font-semibold text-violet-800">
                        awaiting approval
                      </span>
                    )}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      {selected && (
        <section aria-label="Selected job" className="mt-8">
          {detailError ? (
            <InlineRetry message={detailError} onRetry={() => void fetchJob(selected)} />
          ) : !detail ? (
            <p className="text-sm text-muted" role="status">
              Loading job…
            </p>
          ) : (
            <JobDetail detail={detail} onReload={refreshAll} onFail={onFail} />
          )}
        </section>
      )}
    </div>
  );
}

function InlineRetry({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="flex items-center gap-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
      <p role="alert" className="min-w-0 flex-1">
        {message}
      </p>
      <Button variant="subtle" className="h-7" onClick={onRetry}>
        Retry
      </Button>
    </div>
  );
}

function SubmitForm({
  disabled,
  onFail,
  onSubmitted,
}: {
  disabled: boolean;
  onFail: (e: unknown) => void;
  onSubmitted: (job: string) => void;
}) {
  const [goal, setGoal] = useState("");
  const [budget, setBudget] = useState("");
  const [step, setStep] = useState<{ budget: number; model: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const busyRef = useRef(false);

  function parsedBudget(): number | undefined | null {
    const t = budget.trim();
    if (!t) return undefined;
    const n = Number(t);
    return isFinite(n) && n > 0 ? n : null;
  }

  function send(confirm: boolean) {
    if (busyRef.current) return;
    const text = goal.trim();
    const b = parsedBudget();
    if (!text) return setError("Describe the goal first.");
    if (b === null) return setError("Budget must be a positive number of dollars.");
    busyRef.current = true;
    setBusy(true);
    setError("");
    submitAgentJob(text, b, confirm)
      .then((r) => {
        if (r.job) {
          setGoal("");
          setBudget("");
          setStep(null);
          onSubmitted(r.job);
        } else if (r.needs_confirm) {
          setStep({ budget: r.budget ?? 25, model: r.model ?? "" });
        } else {
          setError("The server didn't start a job or ask to confirm. Try again.");
        }
      })
      .catch((e) => {
        onFail(e);
        setError(errorMessage(e, "Could not submit."));
      })
      .finally(() => {
        busyRef.current = false;
        setBusy(false);
      });
  }

  return (
    <Card className="p-4">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (!step) send(false);
        }}
      >
        <label htmlFor="aj-goal" className="text-sm font-medium text-ink">
          New job
        </label>
        <textarea
          id="aj-goal"
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          readOnly={!!step}
          rows={3}
          placeholder="Describe a long-horizon goal"
          className="mt-1 w-full resize-y rounded-lg border border-line bg-white px-3 py-2 text-sm text-ink placeholder:text-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal/40"
        />
        <div className="mt-2 flex flex-wrap items-end gap-3">
          <div>
            <label htmlFor="aj-budget" className="block text-xs text-muted">
              Budget in dollars
            </label>
            <input
              id="aj-budget"
              inputMode="decimal"
              value={budget}
              onChange={(e) => setBudget(e.target.value)}
              readOnly={!!step}
              placeholder="25"
              className="mt-0.5 h-9 w-28 rounded-lg border border-line bg-white px-3 text-sm text-ink placeholder:text-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal/40"
            />
          </div>
          <p className="pb-2 text-xs text-muted">Default $25 per working day.</p>
          {!step && (
            <Button type="submit" variant="primary" disabled={disabled || busy || !goal.trim()} aria-busy={busy}>
              {busy ? "Checking…" : "Submit"}
            </Button>
          )}
        </div>
      </form>

      {step && (
        <div className="mt-4 rounded-xl border border-line bg-canvas p-3">
          <h2 className="text-sm font-medium text-ink">Start this job?</h2>
          <p className="mt-1 text-xs leading-relaxed text-muted">
            Budget {budgetOf(step.budget)}
            {step.model && (
              <>
                {" "}
                · model <span className="mono">{step.model}</span>
              </>
            )}
            . The job stops when the budget is spent. If no runner is configured it is queued and does not run.
          </p>
          <div className="mt-3 flex gap-2">
            <Button variant="primary" onClick={() => send(true)} disabled={disabled || busy} aria-busy={busy}>
              {busy ? "Starting…" : "Start job"}
            </Button>
            <Button
              variant="subtle"
              onClick={() => {
                setStep(null);
                setError("");
              }}
              disabled={busy}
            >
              Edit
            </Button>
          </div>
        </div>
      )}

      {error && (
        <p className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
          {error}
        </p>
      )}
    </Card>
  );
}

function JobDetail({
  detail,
  onReload,
  onFail,
}: {
  detail: AgentJobDetail;
  onReload: () => void;
  onFail: (e: unknown) => void;
}) {
  return (
    <div>
      <header className="mb-4">
        <h2 className="font-display text-xl leading-snug text-ink">
          {detail.goal}
        </h2>
        <p className="mt-2 flex flex-wrap items-center gap-x-3 text-xs">
          <AgentStatusWord status={detail.status} />
          <span className="mono text-muted">
            {cost(detail.spent)} of {budgetOf(detail.budget)}
          </span>
          <span className="mono text-muted">{detail.job}</span>
        </p>
      </header>

      {detail.approvals.length > 0 && (
        <ul aria-label="Pending approvals" className="mb-5 space-y-3">
          {detail.approvals.map((a) => (
            <ApprovalCard key={a.id} job={detail.job} a={a} onReload={onReload} onFail={onFail} />
          ))}
        </ul>
      )}

      <h3 className="mb-2 text-[11px] font-medium uppercase tracking-wider text-muted">Trail</h3>
      {detail.events.length === 0 ? (
        <p className="rounded-2xl border border-dashed border-line px-4 py-6 text-center text-sm text-muted">
          No events yet. The trail fills in as the job runs.
        </p>
      ) : (
        <ol className="divide-y divide-line rounded-2xl border border-line bg-white shadow-card">
          {detail.events.map((row) => (
            <TrailItem key={`${row.seq}-${row.event}`} row={row} />
          ))}
        </ol>
      )}
    </div>
  );
}

const HIDDEN = new Set(["job", "seq", "ts", "event"]);

function fmt(v: unknown): string {
  return typeof v === "string" ? v : JSON.stringify(v);
}

function TrailItem({ row }: { row: TrailRow }) {
  const fields = Object.entries(row).filter(([k]) => !HIDDEN.has(k));
  return (
    <li className="px-4 py-2.5">
      <div className="flex items-baseline justify-between gap-3">
        <p className="mono font-medium text-ink">{row.event}</p>
        <span className="mono shrink-0 text-muted">
          #{row.seq} · {rel(row.ts)}
        </span>
      </div>
      {fields.length > 0 && (
        <p className="mono mt-0.5 whitespace-pre-wrap break-words text-muted">
          {fields.map(([k, v]) => `${k}: ${fmt(v)}`).join("  ")}
        </p>
      )}
    </li>
  );
}

function ApprovalCard({
  job,
  a,
  onReload,
  onFail,
}: {
  job: string;
  a: Approval;
  onReload: () => void;
  onFail: (e: unknown) => void;
}) {
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [stale, setStale] = useState(false);
  const [error, setError] = useState("");
  const inflight = useRef(false);
  const approveRef = useRef<HTMLButtonElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const refocus = useRef(false);

  const expired = a.expires_at <= Date.now() / 1000;
  const twoPress = needsSecondPress(a);

  useEffect(() => {
    if (armed) confirmRef.current?.focus();
    else if (refocus.current) {
      refocus.current = false;
      approveRef.current?.focus();
    }
  }, [armed]);

  function decide(verb: "approve" | "deny") {
    if (inflight.current) return;
    inflight.current = true;
    setBusy(true);
    setArmed(false);
    setError("");
    decideApproval(job, a.id, verb, a.hash)
      .then(() => onReload())
      .catch((e) => {
        onFail(e);
        if (e instanceof ApiError && e.status === 409) {
          setStale(true);
          setError("This approval changed, expired, or was already decided. Reload to review the current request.");
        } else setError(errorMessage(e, "Could not record the decision."));
      })
      .finally(() => {
        inflight.current = false;
        setBusy(false);
      });
  }

  function onApprove() {
    if (twoPress && !armed) setArmed(true);
    else decide("approve");
  }

  function revert() {
    refocus.current = true;
    setArmed(false);
  }

  const titleId = `ap-${a.id}`;
  const locked = busy || stale;
  return (
    <li
      aria-labelledby={titleId}
      onKeyDown={(e) => {
        if (e.key === "Escape" && armed) {
          e.preventDefault();
          revert();
        }
      }}
      className="rounded-2xl border border-violet-200 bg-violet-50/60 p-4"
    >
      <div className="flex items-baseline justify-between gap-3">
        <h4 id={titleId} className="text-sm font-semibold text-ink">
          Approval needed: <span className="mono uppercase">{a.kind}</span>
        </h4>
        <span className="mono text-muted">hash {a.hash.slice(0, 12)}</span>
      </div>
      <pre className="mono mt-2 overflow-x-auto whitespace-pre-wrap break-words rounded-lg bg-white p-3 text-ink/90">
        {JSON.stringify(a.detail, null, 2)}
      </pre>

      {expired ? (
        <p className="mt-3 text-sm text-muted">Expired, denied. This request can no longer be decided.</p>
      ) : (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {armed ? (
            <Button
              ref={confirmRef}
              variant="primary"
              disabled={locked}
              onClick={() => decide("approve")}
              onBlur={() => setArmed(false)}
            >
              Confirm approve {a.kind}
            </Button>
          ) : (
            <Button ref={approveRef} variant="primary" disabled={locked} onClick={onApprove} aria-busy={busy}>
              Approve
            </Button>
          )}
          <Button variant="subtle" disabled={locked} onClick={() => decide("deny")}>
            Deny
          </Button>
          {armed && <span className="text-xs text-muted">Press again to grant. Esc cancels.</span>}
        </div>
      )}

      {error && (
        <div className="mt-3 flex items-center gap-3 text-sm text-red-700">
          <p role="alert" className="min-w-0 flex-1">
            {error}
          </p>
          {stale && (
            <Button
              variant="subtle"
              className="h-7"
              onClick={() => {
                setStale(false);
                setError("");
                onReload();
              }}
            >
              Reload
            </Button>
          )}
        </div>
      )}
    </li>
  );
}
