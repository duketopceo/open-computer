import { PageShell } from "@/components/PageShell";
import { DataState } from "@/components/DataState";
import { Empty, Td, Table, Time } from "@/components/DataTable";
import { Button } from "@/components/primitives";
import { getOverview } from "@/lib/api";
import { useFetch } from "@/lib/useFetch";

export default function Overview() {
  const { data, error, loading, reload } = useFetch(getOverview, []);
  return (
    <PageShell title="Overview" sub="What needs you, and what is running.">
      <div className="mb-4 flex items-center gap-3">
        <Button variant="subtle" onClick={reload} disabled={loading}>
          Refresh
        </Button>
      </div>
      <DataState loading={loading} error={error} hasData={!!data} onRetry={reload} />
      {data && (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            <Stat label="Waiting on you" value={String(data.pending_approvals.length)} href="/approvals" />
            <Stat
              label="Spend today"
              value={`$${data.spend.toFixed(4)}`}
              sub={`cap $${data.cap.toFixed(2)}`}
            />
            <Stat label="Runs" value={String(data.runs.length)} href="/runs" />
          </div>
          <section>
            <h2 className="mb-2 text-sm font-medium text-ink">Recent activity</h2>
            {data.audit.length === 0 ? (
              <Empty title="Nothing yet">Audit events show up here as work happens.</Empty>
            ) : (
              <Table caption="Recent audit events" head={["Event", "Detail", "When"]}>
                {data.audit.map((row, i) => (
                  <tr key={i}>
                    <Td mono>{String(row.event ?? "event")}</Td>
                    <Td>{auditDetail(row)}</Td>
                    <Td>
                      <Time ts={Number(row.ts ?? 0)} />
                    </Td>
                  </tr>
                ))}
              </Table>
            )}
          </section>
        </div>
      )}
    </PageShell>
  );
}

function Stat({ label, value, sub, href }: { label: string; value: string; sub?: string; href?: string }) {
  const inner = (
    <>
      <p className="text-2xl font-semibold text-ink">{value}</p>
      <p className="text-xs text-muted">
        {label}
        {sub ? ` · ${sub}` : ""}
      </p>
    </>
  );
  return (
    <div className="rounded-xl border border-line bg-white p-4 shadow-card">
      {href ? (
        <a href={href} className="block hover:opacity-80">
          {inner}
        </a>
      ) : (
        inner
      )}
    </div>
  );
}

function auditDetail(row: Record<string, unknown>): string {
  for (const k of ["task", "subtask", "run", "goal", "decision", "reason", "error"]) {
    const v = row[k];
    if (typeof v === "string" && v) return v.slice(0, 80);
  }
  return "";
}
