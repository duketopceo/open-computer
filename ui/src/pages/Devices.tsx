import { PageShell } from "@/components/PageShell";
import { DataState } from "@/components/DataState";
import { Empty, StatusWord, Table, Td, Time } from "@/components/DataTable";
import { Button } from "@/components/primitives";
import { getDevices } from "@/lib/api";
import { useFetch } from "@/lib/useFetch";

export default function Devices() {
  const { data, error, loading, reload } = useFetch(getDevices, []);
  return (
    <PageShell
      title="Devices"
      sub="Paired Personal Computer devices: reachability, grants and queue depth."
    >
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Button variant="subtle" onClick={reload} disabled={loading}>
          Refresh
        </Button>
        {data && (
          <p className="text-sm text-muted">
            <b className="text-ink">{data.eligible}</b> of <b className="text-ink">{data.total}</b> eligible now (online and awake);{" "}
            <b className="text-ink">{data.parked_depth}</b> task(s) parked waiting for a device to come back.
          </p>
        )}
      </div>
      <DataState loading={loading} error={error} hasData={!!data} onRetry={reload} />
      {data &&
        (data.devices.length === 0 ? (
          <Empty title="No devices paired">Start a device agent to register one.</Empty>
        ) : (
          <Table
            caption="Devices, state and queue depth"
            head={["Device", "Capabilities", "Granted roots", "State", "Heartbeat", "Grants", "Inbox"]}
          >
            {data.devices.map((d) => (
              <tr key={d.device_id}>
                <Td mono>
                  {d.device_id}
                  <div className="text-xs text-muted">{d.os}</div>
                </Td>
                <Td className="text-muted">{d.capabilities.join(", ") || "—"}</Td>
                <Td className="text-xs text-muted">{d.roots.join(", ") || "—"}</Td>
                <Td>
                  <StatusWord word={d.state} />
                </Td>
                <Td>
                  <Time ts={d.last_heartbeat} />
                </Td>
                <Td>{d.active_grants}</Td>
                <Td>{d.inbox_depth}</Td>
              </tr>
            ))}
          </Table>
        ))}
    </PageShell>
  );
}
