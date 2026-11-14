import { PageShell } from "@/components/PageShell";
import { DataState } from "@/components/DataState";
import { Empty, StatusWord, Table, Td } from "@/components/DataTable";
import { Button } from "@/components/primitives";
import { getSkills } from "@/lib/api";
import { useFetch } from "@/lib/useFetch";

export default function Skills() {
  const { data, error, loading, reload } = useFetch(getSkills, []);
  const skills = data?.skills ?? [];
  const loadError = data?.error;
  return (
    <PageShell title="Skills" sub="The catalog on disk — what a run can declare, and whether this build can honour it.">
      <div className="mb-4 flex items-center gap-3">
        <Button variant="subtle" onClick={reload} disabled={loading}>
          Refresh
        </Button>
      </div>
      <DataState loading={loading} error={error} hasData={!!data} onRetry={reload} />
      {loadError && (
        <p className="mb-4 text-xs text-muted" role="status">
          The catalog did not load: {loadError}
        </p>
      )}
      {data &&
        (skills.length === 0 ? (
          <Empty title="No skills yet">A directory under the catalog with a SKILL.md appears here.</Empty>
        ) : (
          <Table caption="Skills" head={["Name", "Needs", "Status", "Description"]}>
            {skills.map((s) => (
              <tr key={s.name}>
                <Td mono>{s.name}</Td>
                <Td mono>
                  {s.pulls_tools.length === 0
                    ? "—"
                    : s.pulls_tools.map((t) => (s.missing.includes(t) ? `${t} (missing)` : t)).join(", ")}
                </Td>
                <Td>
                  <StatusWord word={s.status} />
                </Td>
                <Td className="text-xs text-muted">{s.description}</Td>
              </tr>
            ))}
          </Table>
        ))}
    </PageShell>
  );
}
