import { useState } from "react";
import { PageShell } from "@/components/PageShell";
import { DataState } from "@/components/DataState";
import { Empty, StatusWord, Time } from "@/components/DataTable";
import { Button } from "@/components/primitives";
import { decideQueueItem, getApprovals, type ApprovalItem } from "@/lib/api";
import { useFetch } from "@/lib/useFetch";

export default function Approvals() {
  const { data, error, loading, reload } = useFetch(getApprovals, []);
  const items = data?.approvals ?? [];
  const pending = items.filter((i) => i.status === "pending");
  const decided = items.filter((i) => i.status !== "pending");
  return (
    <PageShell
      title="Approvals"
      sub="Human gate: approve or send back requests that want to act outside the box."
    >
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
        {pending.map((i) => (
          <Card key={i.id} item={i} onDone={reload} />
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
                  <span className="mono text-muted">{i.id}</span> {i.kind || ""} ·{" "}
                  <Time ts={i.decided_ts ?? i.ts} />
                </li>
              ))}
          </ul>
        </details>
      )}
    </PageShell>
  );
}

function Card({ item, onDone }: { item: ApprovalItem; onDone: () => void }) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<"approve" | "send-back" | null>(null);
  const [err, setErr] = useState("");

  async function decide(verb: "approve" | "send-back") {
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
    <li className="rounded-xl border border-line bg-white p-4 shadow-card">
      <div className="flex items-baseline justify-between gap-3">
        <p className="text-sm font-medium text-ink">{item.kind || "Approval request"}</p>
        <span className="mono text-xs text-muted">
          {item.id} · <Time ts={item.ts} />
        </span>
      </div>
      {item.reason && <p className="mt-1 text-xs text-muted">{item.reason}</p>}
      {item.payload != null && (
        <details className="mt-2">
          <summary className="cursor-pointer text-xs text-muted">What it will do</summary>
          <pre className="mt-1 max-h-40 overflow-auto rounded-lg bg-canvas p-3 text-xs">
            {JSON.stringify(item.payload, null, 2)}
          </pre>
        </details>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button onClick={() => decide("approve")} disabled={busy !== null}>
          {busy === "approve" ? "Approving…" : "Approve"}
        </Button>
        <input
          className="min-w-48 flex-1 rounded-lg border border-line bg-white px-3 py-1.5 text-sm"
          placeholder="Note for send-back (what to change)"
          value={note}
          onChange={(e) => setNote(e.target.value)}
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
