import { PageShell } from "@/components/PageShell";
import { DataState } from "@/components/DataState";
import { Empty, Table, Td, Time } from "@/components/DataTable";
import { Button } from "@/components/primitives";
import { listRuns, type RunSummary } from "@/lib/api";
import { useFetch } from "@/lib/useFetch";

export default function Runs() {
  const { data, error, loading, reload } = useFetch(listRuns, []);
  const runs = data ?? [];
  return (
    <PageShell title="Runs" sub="Every goal you've sent: state, steps, and when it started.">
      <div className="mb-4 flex items-center gap-3">
        <Button variant="subtle" onClick={reload} disabled={loading}>
          Refresh
        </Button>
        <p className="text-sm text-muted">
          <b className="text-ink">{runs.length}</b> runs
        </p>
      </div>
      <DataState loading={loading} error={error} hasData={!!data} onRetry={reload} />
      {data &&
        (runs.length === 0 ? (
          <Empty title="No runs yet">Describe a goal on the Work page and it shows up here.</Empty>
        ) : (
          <Table caption="Runs" head={["Goal", "State", "Steps", "Started"]}>
            {runs.map((r) => (
              <tr key={r.run}>
                <Td>
                  <a className="text-teal underline-offset-2 hover:underline" href={`/#${r.run}`}>
                    {r.goal.length > 80 ? r.goal.slice(0, 80) + "…" : r.goal}
                  </a>
                  <div className="mono text-xs text-muted">{r.run}</div>
                </Td>
                <Td>{stateWords(r)}</Td>
                <Td mono>{r.subtasks}</Td>
                <Td>
                  <Time ts={r.ts} />
                </Td>
              </tr>
            ))}
          </Table>
        ))}
    </PageShell>
  );
}

function stateWords(r: RunSummary): string {
  const s = r.states;
  if (s.FAILED) return `${s.FAILED} failed`;
  if (s.RUNNING || s.LEASED) return "running";
  if (s["AWAITING-APPROVAL"]) return "waiting on you";
  if (s.DONE && s.DONE === s.DONE + s.QUEUED) return "done";
  if (s.DONE) return `${s.DONE} done`;
  return "queued";
}
