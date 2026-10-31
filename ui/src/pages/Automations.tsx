import { useState } from "react";
import { PageShell } from "@/components/PageShell";
import { DataState } from "@/components/DataState";
import { Empty, Table, Td, Time } from "@/components/DataTable";
import { Button } from "@/components/primitives";
import { autoAction, getAutomations, newAutomation, type Automation } from "@/lib/api";
import { useFetch } from "@/lib/useFetch";

export default function Automations() {
  const { data, error, loading, reload } = useFetch(getAutomations, []);
  const items = data?.automations ?? [];
  return (
    <PageShell title="Automations" sub="Goals on a cadence — durable Temporal schedules.">
      <div className="mb-4 flex items-center gap-3">
        <Button variant="subtle" onClick={reload} disabled={loading}>
          Refresh
        </Button>
        {data && !data.enabled && (
          <p className="text-sm text-muted">Scheduler offline — list is what was last seen.</p>
        )}
      </div>
      <DataState loading={loading} error={error} hasData={!!data} onRetry={reload} />
      {data && (
        <>
          <NewForm onDone={reload} />
          {items.length === 0 ? (
            <Empty title="No schedules">Create one above — a goal that runs on a cadence.</Empty>
          ) : (
            <Table caption="Automations" head={["Goal", "Every", "Next", "State", ""]}>
              {items.map((a) => (
                <Row key={a.id} a={a} onDone={reload} />
              ))}
            </Table>
          )}
        </>
      )}
    </PageShell>
  );
}

function NewForm({ onDone }: { onDone: () => void }) {
  const [goal, setGoal] = useState("");
  const [mins, setMins] = useState("60");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit() {
    const every = parseInt(mins, 10);
    if (!goal.trim() || !every || every < 5) {
      setErr("A goal and at least 5 minutes between runs.");
      return;
    }
    setBusy(true);
    setErr("");
    try {
      await newAutomation(goal.trim(), every);
      setGoal("");
      onDone();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="mb-4 flex flex-wrap items-center gap-2 rounded-xl border border-line bg-white p-3 shadow-card">
      <input
        className="min-w-56 flex-1 rounded-lg border border-line px-3 py-1.5 text-sm"
        placeholder="Goal to run on a schedule"
        value={goal}
        onChange={(e) => setGoal(e.target.value)}
      />
      <input
        className="w-24 rounded-lg border border-line px-3 py-1.5 text-sm"
        type="number"
        min={5}
        value={mins}
        onChange={(e) => setMins(e.target.value)}
        title="Every N minutes"
      />
      <Button onClick={submit} disabled={busy}>
        {busy ? "Creating…" : "Create"}
      </Button>
      {err && <p className="w-full text-xs text-red-600">{err}</p>}
    </div>
  );
}

function Row({ a, onDone }: { a: Automation; onDone: () => void }) {
  const [busy, setBusy] = useState("");
  async function act(verb: "pause" | "trigger" | "delete") {
    setBusy(verb);
    try {
      await autoAction(a.id, verb);
      onDone();
    } finally {
      setBusy("");
    }
  }
  return (
    <tr>
      <Td>{a.goal.length > 70 ? a.goal.slice(0, 70) + "…" : a.goal}</Td>
      <Td mono>{a.every_minutes}m</Td>
      <Td>{a.next_run ? <Time ts={a.next_run} /> : "—"}</Td>
      <Td>{a.paused ? "paused" : a.state || "active"}</Td>
      <Td>
        <span className="flex gap-1">
          <Button variant="subtle" onClick={() => act("trigger")} disabled={!!busy}>
            Run now
          </Button>
          <Button variant="subtle" onClick={() => act("pause")} disabled={!!busy}>
            {a.paused ? "Resume" : "Pause"}
          </Button>
          <Button variant="subtle" onClick={() => act("delete")} disabled={!!busy}>
            Delete
          </Button>
        </span>
      </Td>
    </tr>
  );
}
