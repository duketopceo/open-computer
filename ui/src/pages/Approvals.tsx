import { useEffect, useRef, useState, type ReactNode } from "react";
import { PageShell } from "@/components/PageShell";
import { DataState } from "@/components/DataState";
import { Empty, StatusWord, Time } from "@/components/DataTable";
import { Button } from "@/components/primitives";
import { approvalRisk, decideQueueItem, getApprovals, type ApprovalItem } from "@/lib/api";
import { cost } from "@/lib/format";
import { useFetch } from "@/lib/useFetch";

export default function Approvals() {
  const { data, error, loading, reload } = useFetch(getApprovals, []);
  const items = data?.approvals ?? [];
  const pending = items.filter((i) => i.status === "pending");
  const decided = items.filter((i) => i.status !== "pending");
  const [sel, setSel] = useState(0);
  const cur = Math.min(sel, Math.max(0, pending.length - 1));
  useApprovalChords(pending.length, cur, setSel);
  return (
    <PageShell
      title="Approvals"
      sub="Human gate: approve or send back requests that want to act outside the box."
    >
      {pending.length > 0 && (
        <p className="mb-3 text-xs text-muted">
          Keys: <kbd className="mono">j</kbd>/<kbd className="mono">k</kbd> move, <kbd className="mono">a</kbd> approves,{" "}
          <kbd className="mono">r</kbd> sends back with a note (<kbd className="mono">ctrl+enter</kbd> sends).
        </p>
      )}
      <div className="mb-4 flex items-center gap-3">
        <Button variant="subtle" onClick={reload} disabled={loading}>
          Refresh
        </Button>
        <p className="text-sm text-muted">
          <b className="text-ink">{pending.length}</b> waiting · {decided.length} decided
        </p>
      </div>
      <DataState loading={loading} error={error} hasData={!!data} onRetry={reload} />
      {pending.length === 0 && !loading && (
        <Empty title="Nothing waiting">Requests that need a human show up here.</Empty>
      )}
      <ul className="space-y-3">
        {pending.map((i, n) => (
          <Card key={i.id} item={i} onDone={reload} selected={n === cur} onSelect={() => setSel(n)} />
        ))}
      </ul>
      {decided.length > 0 && (
        <details className="mt-6">
          <summary className="cursor-pointer text-xs text-muted">History — {decided.length}</summary>
          <ul className="mt-2 space-y-2">
            {decided
              .slice()
              .reverse()
              .slice(0, 20)
              .map((i) => (
                <li key={i.id} className="rounded-lg border border-line bg-white px-3 py-2 text-xs">
                  <StatusWord word={i.status === "approved" ? "approved" : "sent back"} />{" "}
                  <span className="mono text-muted">{i.id}</span> {i.what || ""}
                  {i.egress ? <> → <span className="mono">{i.egress}</span></> : null} ·{" "}
                  <Time ts={i.decided_ts ?? i.ts} />
                </li>
              ))}
          </ul>
        </details>
      )}
    </PageShell>
  );
}

/** j/k move the selected card, a approves it, r jumps to its note. Ignored while typing or with modifiers. */
function useApprovalChords(count: number, cur: number, setSel: (n: number) => void) {
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.ctrlKey || e.metaKey || e.altKey || e.defaultPrevented || count === 0) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
      if (e.key === "j") setSel(Math.min(count - 1, cur + 1));
      else if (e.key === "k") setSel(Math.max(0, cur - 1));
      else if (e.key === "a" || e.key === "r") {
        const card = document.querySelectorAll<HTMLElement>("[data-approval]")[cur];
        if (!card) return;
        e.preventDefault();
        if (e.key === "a") card.querySelector<HTMLButtonElement>("[data-act=approve]")?.click();
        else card.querySelector<HTMLInputElement>("[data-act=note]")?.focus();
        return;
      } else return;
      e.preventDefault();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [count, cur, setSel]);
}

const RISK_STYLE = { high: "text-red-700", elevated: "text-amber-700", low: "text-teal" } as const;
const RISK_GLYPH = { high: "✕", elevated: "▲", low: "✓" } as const;

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs uppercase tracking-wider text-muted">{label}</dt>
      <dd className="mt-0.5 break-words text-sm text-ink">{children}</dd>
    </div>
  );
}

function Card({
  item,
  onDone,
  selected,
  onSelect,
}: {
  item: ApprovalItem;
  onDone: () => void;
  selected: boolean;
  onSelect: () => void;
}) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<"approve" | "send-back" | null>(null);
  const [err, setErr] = useState("");
  const ref = useRef<HTMLLIElement>(null);
  const risk = approvalRisk(item);
  const price = cost(item.est_cost ?? 0);

  useEffect(() => {
    if (selected) ref.current?.scrollIntoView?.({ block: "nearest" });
  }, [selected]);

  async function decide(verb: "approve" | "send-back") {
    if (busy) return;
    if (verb === "send-back" && !note.trim()) {
      setErr("Add a note so the agent knows what to change.");
      return;
    }
    setBusy(verb);
    setErr("");
    try {
      await decideQueueItem(item.id, verb, item.payload_hash, note.trim() || undefined);
      onDone();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <li
      ref={ref}
      data-approval={item.id}
      aria-current={selected ? "true" : undefined}
      onClick={onSelect}
      onFocus={onSelect}
      className={
        "rounded-xl border bg-white p-4 shadow-card " + (selected ? "border-teal ring-2 ring-teal/30" : "border-line")
      }
    >
      <div className="flex items-baseline justify-between gap-3">
        <p className="text-sm font-medium text-ink">{item.what || "Approval request"}</p>
        <span className="text-xs text-muted">
          <span className="mono">{item.id}</span> · <Time ts={item.ts} />
        </span>
      </div>
      <dl className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Fact label="Who asked">
          {item.who || "agent"}
          {item.session ? <span className="mono text-xs text-muted"> · {item.session}</span> : null}
        </Fact>
        <Fact label="Leaves this box for">
          <span className="mono">{item.egress || "unknown"}</span>
          {item.model ? <span className="text-xs text-muted"> · model {item.model}</span> : null}
        </Fact>
        <Fact label="Estimated cost">{price}</Fact>
        <Fact label="Risk">
          <span className={RISK_STYLE[risk.level]}>
            <span aria-hidden>{RISK_GLYPH[risk.level]}</span> {risk.level[0].toUpperCase() + risk.level.slice(1)} risk
          </span>
          <span className="block text-xs text-muted">{risk.why}</span>
        </Fact>
      </dl>
      {item.payload_hash && (
        <p className="mt-3 text-xs text-muted">
          This exact payload (sha256 <span className="mono">{item.payload_hash.slice(0, 12)}</span>
          {item.payload_bytes != null ? `, ${item.payload_bytes} bytes` : ""}) goes to{" "}
          <span className="mono">{item.egress || "unknown"}</span> if you approve. Any change makes it a new request.
        </p>
      )}
      {item.payload != null && (
        <details className="mt-2">
          <summary className="cursor-pointer text-xs text-muted">Show the payload</summary>
          <pre
            tabIndex={0}
            aria-label={`Payload for ${item.id}`}
            className="mt-1 max-h-40 overflow-auto rounded-lg bg-canvas p-3 text-xs"
          >
            {JSON.stringify(item.payload, null, 2)}
          </pre>
        </details>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button data-act="approve" aria-keyshortcuts="a" onClick={() => decide("approve")} disabled={busy !== null}>
          {busy === "approve" ? "Approving…" : `Approve ${price}`}
        </Button>
        <input
          data-act="note"
          aria-label={`Note for send-back on ${item.id}`}
          aria-keyshortcuts="r"
          className="min-w-48 flex-1 rounded-lg border border-line bg-white px-3 py-1.5 text-sm"
          placeholder="Note for send-back (what to change)"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
              e.preventDefault();
              void decide("send-back");
            }
          }}
        />
        <Button variant="subtle" onClick={() => decide("send-back")} disabled={busy !== null}>
          {busy === "send-back" ? "Sending…" : "Send back"}
        </Button>
      </div>
      {err && (
        <p className="mt-2 text-xs text-red-600" role="alert">
          {err}
        </p>
      )}
    </li>
  );
}
