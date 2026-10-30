import { PageShell } from "@/components/PageShell";
import { DataState } from "@/components/DataState";
import { Empty } from "@/components/DataTable";
import { getSandboxes } from "@/lib/api";
import { Link } from "@/lib/router";
import { useFetch } from "@/lib/useFetch";
import { SandboxStates, sandboxId } from "./Sandboxes";

// The detail reads the list payload (KTD6): no per-id endpoint, no second control-plane call.
export default function SandboxDetail({ id }: { id: string }) {
  const { data, error, loading, reload } = useFetch(getSandboxes, []);
  const row = data?.sandboxes.find((s) => sandboxId(s) === id);
  const back = (
    <Link href="/sandboxes" className="text-sm font-medium text-teal underline underline-offset-2">
      Back to sandboxes
    </Link>
  );
  return (
    <PageShell title="Sandbox" sub={id}>
      <DataState loading={loading} error={error} hasData={!!data} onRetry={reload} />
      {data && !row && (
        <div className="space-y-3">
          {data.configured && data.up ? (
            <Empty title="Sandbox not found">{id} is not in the current inventory. It may have been reclaimed.</Empty>
          ) : (
            <SandboxStates data={data} />
          )}
          {back}
        </div>
      )}
      {row && (
        <div className="space-y-3">
          <div className="overflow-x-auto rounded-2xl border border-line bg-white shadow-card">
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Sandbox {id}</caption>
              <tbody className="divide-y divide-line">
                {Object.entries(row)
                  .sort(([a], [b]) => a.localeCompare(b))
                  .map(([k, v]) => (
                    <tr key={k}>
                      <th scope="row" className="w-48 px-3 py-2 align-top font-medium text-muted">
                        {k}
                      </th>
                      <td className="mono break-all px-3 py-2">{typeof v === "string" ? v : JSON.stringify(v)}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
          {back}
        </div>
      )}
    </PageShell>
  );
}
