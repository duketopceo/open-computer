import { PageShell } from "@/components/PageShell";
import { DataState } from "@/components/DataState";
import { Empty, StatusWord, Table, Td } from "@/components/DataTable";
import { Button } from "@/components/primitives";
import { getSkills } from "@/lib/api";
import { useFetch } from "@/lib/useFetch";

export default function Skills() {
  const { data, error, loading, reload } = useFetch(getSkills, []);
  const skills = data?.skills ?? [];
  return (
    <PageShell title="Skills" sub="Capability surface — live health, never aspiration.">
      <div className="mb-4 flex items-center gap-3">
        <Button variant="subtle" onClick={reload} disabled={loading}>
          Refresh
        </Button>
      </div>
      <DataState loading={loading} error={error} hasData={!!data} onRetry={reload} />
      {data &&
        (skills.length === 0 ? (
          <Empty title="Nothing registered">Capabilities report here as services come up.</Empty>
        ) : (
          <Table caption="Skills" head={["Name", "State", "Detail"]}>
            {skills.map((s, i) => {
              const name = String(s.name ?? s.id ?? `skill-${i}`);
              const state = String(s.state ?? s.status ?? "unknown");
              return (
                <tr key={name}>
                  <Td mono>{name}</Td>
                  <Td>
                    <StatusWord word={state} />
                  </Td>
                  <Td className="text-xs text-muted">{detail(s)}</Td>
                </tr>
              );
            })}
          </Table>
        ))}
    </PageShell>
  );
}

function detail(s: Record<string, unknown>): string {
  for (const k of ["detail", "note", "error", "summary", "version"]) {
    const v = s[k];
    if (typeof v === "string" && v) return v.slice(0, 90);
  }
  return "";
}
