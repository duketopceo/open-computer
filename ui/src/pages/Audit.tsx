import { useEffect, useState, type FormEvent } from "react";
import { PageShell } from "@/components/PageShell";
import { DataState } from "@/components/DataState";
import { Empty, StatusWord, Table, Td, Time } from "@/components/DataTable";
import { Button } from "@/components/primitives";
import { auditQuery, getAudit, type AuditFilter } from "@/lib/api";
import { cn } from "@/lib/cn";
import { Link, useRouter } from "@/lib/router";
import { useFetch } from "@/lib/useFetch";

const FILTERS: [AuditFilter, string][] = [
  ["all", "All"],
  ["blocked", "Blocked"],
  ["allowed", "Allowed"],
  ["decisions", "Decisions"],
];

const href = (f: AuditFilter, q: string) => `/audit${auditQuery(f, q)}`;

export default function Audit() {
  const { search, navigate } = useRouter();
  const params = new URLSearchParams(search);
  const rawF = params.get("f") ?? "all";
  const f = (FILTERS.some(([k]) => k === rawF) ? rawF : "all") as AuditFilter;
  const q = (params.get("q") ?? "").slice(0, 200);
  const [text, setText] = useState(q);
  useEffect(() => setText(q), [q]);

  const { data, error, loading, reload } = useFetch(() => getAudit(f, q), [f, q]);

  function submit(e: FormEvent) {
    e.preventDefault();
    navigate(href(f, text.trim()));
  }

  return (
    <PageShell title="Audit" sub="Append-only: blocked and allowed calls and human decisions, newest first.">
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div role="group" aria-label="Filter" className="flex gap-1.5">
          {FILTERS.map(([k, label]) => (
            <Link
              key={k}
              href={href(k, q)}
              aria-current={k === f ? "true" : undefined}
              className={cn(
                "inline-flex h-7 items-center rounded-full border px-3 text-xs font-medium",
                k === f ? "border-teal/30 bg-teal-soft text-teal" : "border-line text-muted hover:bg-black/5",
              )}
            >
              {label}
            </Link>
          ))}
        </div>
        <form onSubmit={submit} className="flex items-center gap-2">
          <label htmlFor="audit-q" className="sr-only">
            Filter audit rows
          </label>
          <input
            id="audit-q"
            type="search"
            value={text}
            onChange={(e) => setText(e.target.value)}
            maxLength={200}
            placeholder="Filter by text, session or model"
            className="h-9 w-64 max-w-full rounded-lg border border-line bg-white px-3 text-sm text-ink placeholder:text-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal/40"
          />
          <Button type="submit" variant="subtle">
            Filter
          </Button>
        </form>
      </div>
      <DataState loading={loading} error={error} hasData={!!data} onRetry={reload} />
      {data &&
        (data.rows.length === 0 ? (
          <Empty title="No matching events">
            The audit log is append-only. Blocked and allowed calls and human decisions appear here as they happen.
          </Empty>
        ) : (
          <>
            <Table caption="Audit events, newest first" head={["When", "Result", "Event", "Detail"]}>
              {data.rows.map((r, i) => (
                <tr key={`${r.ts}-${i}`}>
                  <Td>
                    <Time ts={r.ts} />
                  </Td>
                  <Td>
                    <StatusWord word={r.result} />
                  </Td>
                  <Td mono>{r.event}</Td>
                  <Td mono className="break-all text-muted">
                    {r.detail}
                  </Td>
                </tr>
              ))}
            </Table>
            <p className="mt-2 text-xs text-muted">
              Showing the latest {data.shown} of {data.total} matching events.
            </p>
          </>
        ))}
    </PageShell>
  );
}
