import { useEffect, useRef } from "react";
import type { GoalOptions, ThreadDetail, ThreadMessage } from "@/lib/api";
import { cn } from "@/lib/cn";
import { Composer } from "./Composer";
import { Reveal } from "./Reveal";
import { RunView } from "./RunView";

/** One conversation: messages top to bottom, runs shown inline where the orchestrator started them, one box below. */
export function ThreadView({
  thread,
  onSend,
  busy,
  disabled,
  error,
}: {
  thread: ThreadDetail;
  onSend: (text: string, opts: GoalOptions) => void;
  busy: boolean;
  disabled: boolean;
  error: string;
}) {
  const end = useRef<HTMLDivElement>(null);
  // Only messages that arrive after this view opened get the text reveal.
  const loaded = useRef(thread.messages.length);
  const runCount = Object.values(thread.runs).filter(Boolean).length;
  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" });
  }, [thread.messages.length, thread.working, runCount]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex-1 overflow-y-auto" tabIndex={0}>
        <ol className="mx-auto flex w-full max-w-3xl flex-col gap-5 px-6 py-8" aria-label="Conversation">
          {thread.messages.map((m, i) => (
            <li key={i} className="animate-fade-up">
              <Message m={m} thread={thread} fresh={i >= loaded.current} />
            </li>
          ))}
          {thread.working && (
            <li className="flex items-center gap-2 text-sm text-muted" role="status">
              <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-line border-t-accent" />
              Working…
            </li>
          )}
        </ol>
        <div ref={end} />
      </div>
      <div className="mx-auto w-full max-w-3xl px-6 pb-6">
        {error && (
          <p className="mb-2 rounded-lg border border-urgent bg-urgent-soft px-3 py-2 text-sm text-urgent" role="alert">
            {error}
          </p>
        )}
        <Composer
          onSend={onSend}
          busy={busy}
          working={thread.working}
          disabled={disabled || thread.working}
          placeholder={thread.working ? "Working on it…" : "Reply"}
          rows={2}
          autoFocus
        />
      </div>
    </div>
  );
}

function Message({ m, thread, fresh }: { m: ThreadMessage; thread: ThreadDetail; fresh: boolean }) {
  if (m.role === "user") {
    return (
      <p className="ml-auto w-fit max-w-[85%] whitespace-pre-wrap rounded-2xl bg-surface-3 px-4 py-2.5 text-[15px] leading-relaxed text-ink">
        {m.text}
      </p>
    );
  }
  if (m.role === "run") {
    const run = m.run ? thread.runs[m.run] : null;
    return run ? (
      <div className="fx-edge rounded-2xl border border-line bg-surface-1 p-4 shadow-card">
        <RunView run={run} compact />
      </div>
    ) : (
      <p className="text-sm text-muted">This run was removed.</p>
    );
  }
  return (
    <p
      className={cn(
        "max-w-[85%] whitespace-pre-wrap text-[15px] leading-relaxed",
        m.error ? "text-urgent" : "text-ink",
      )}
    >
      <Reveal text={m.text} animate={fresh && !m.error} />
    </p>
  );
}
