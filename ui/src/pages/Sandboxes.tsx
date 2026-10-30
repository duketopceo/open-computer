import { PageShell } from "@/components/PageShell";
import { DataState } from "@/components/DataState";
import { Empty, StatusWord, Table, Td } from "@/components/DataTable";
import { Button } from "@/components/primitives";
import { getSandboxes, type SandboxesPayload } from "@/lib/api";
import { Link } from "@/lib/router";
import { useFetch } from "@/lib/useFetch";

export const sandboxId = (s: Record<string, unknown>) => String(s.sandboxID ?? "?");

/** The control-plane states the old page told honestly: unconfigured, down (unknown, not empty), empty, listed. */
export function SandboxStates({ data }: { data: SandboxesPayload }) {
  if (!data.configured)
    return (
      <Empty title="Control plane not configured">
        Set <code className="mono">CUBE_API_URL</code> and <code className="mono">CUBE_API_KEY</code> to list sandboxes. Nothing here is guessed.
      </Empty>
    );
  if (!data.up)
    return (
      <Empty title="Control plane is down">
        {data.api_host} did not answer. The sandbox inventory is unknown, not empty. Check that CubeSandbox is running, then refresh.
      </Empty>
    );
  if (data.sandboxes.length === 0)
    return <Empty title="No sandboxes running">A sandbox appears here while a run holds it. Start one with a matrix run.</Empty>;
  return null;
}

export default function Sandboxes() {
  const { data, error, loading, reload } = useFetch(getSandboxes, []);
  const rows = data?.configured && data.up ? data.sandboxes : [];
  return (
    <PageShell
      title="Sandboxes"
      sub={data?.configured ? `Inventory read from ${data.api_host}. Read only.` : "Inventory of running sandboxes."}
    >
      <div className="mb-4">
        <Button variant="subtle" onClick={reload} disabled={loading}>
          Refresh
        </Button>
      </div>
      <DataState loading={loading} error={error} hasData={!!data} onRetry={reload} />
      {data && <SandboxStates data={data} />}
      {rows.length > 0 && (
        <Table caption="Sandboxes" head={["Sandbox", "Template", "State"]}>
          {rows.map((s) => (
            <tr key={sandboxId(s)}>
              <Td mono>
                <Link href={`/sandboxes/${encodeURIComponent(sandboxId(s))}`} className="text-teal underline underline-offset-2">
                  {sandboxId(s)}
                </Link>
              </Td>
              <Td mono>{String(s.templateID ?? "?")}</Td>
              <Td>
                <StatusWord word={String(s.status ?? "unknown")} />
              </Td>
            </tr>
          ))}
        </Table>
      )}
    </PageShell>
  );
}
