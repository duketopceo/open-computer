import { useEffect, useState } from "react";
import { getArtifacts, getArtifactText, artifactUrl, type ArtifactItem } from "@/lib/api";

const size = (n: number) => (n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`);

/** Files a run produced: preview as text, download one, or export all as a zip. `refresh` changes when steps finish. */
export function Artifacts({ runId, refresh }: { runId: string; refresh: number }) {
  const [items, setItems] = useState<ArtifactItem[]>([]);
  useEffect(() => {
    let dead = false;
    getArtifacts(runId)
      .then((a) => !dead && setItems(a))
      .catch(() => !dead && setItems([]));
    return () => {
      dead = true;
    };
  }, [runId, refresh]);
  if (items.length === 0) return null;
  const ok = items.filter((i) => !i.refused);
  return (
    <section aria-label="Files from this run" className="mt-6 rounded-2xl border border-line bg-surface-1 shadow-card" data-testid="artifacts">
      <header className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
        <h2 className="text-sm font-medium text-ink">Files</h2>
        {ok.length > 1 && (
          <a
            href={artifactUrl(runId, "files.zip")}
            download
            className="inline-flex h-7 items-center rounded-full border border-line px-3 text-xs font-medium text-ink hover:bg-surface-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            Download all (.zip)
          </a>
        )}
      </header>
      <ul className="divide-y divide-line">
        {items.map((a) => (
          <FileRow key={a.name} runId={runId} a={a} />
        ))}
      </ul>
    </section>
  );
}

function FileRow({ runId, a }: { runId: string; a: ArtifactItem }) {
  const [text, setText] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const open = (e: React.SyntheticEvent<HTMLDetailsElement>) => {
    if (e.currentTarget.open && text === null)
      getArtifactText(runId, a.name)
        .then(setText)
        .catch(() => setErr("Could not load this file."));
  };
  if (a.refused)
    return (
      <li className="px-4 py-3 text-sm">
        <span className="mono text-muted">{a.name}</span> <span className="text-attention">not saved: {a.refused}</span>
      </li>
    );
  return (
    <li className="px-4 py-3">
      <div className="flex items-center gap-3">
        <details className="min-w-0 flex-1" onToggle={open}>
          <summary className="cursor-pointer select-none truncate text-sm text-ink">
            <span className="mono">{a.name}</span> <span className="text-xs text-muted">· {size(a.size ?? 0)}</span>
          </summary>
          {err ? (
            <p role="alert" className="mt-2 text-xs text-urgent">
              {err}
            </p>
          ) : (
            <pre tabIndex={0} aria-label={`Preview of ${a.name}`} className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap rounded-lg bg-canvas p-3 text-xs leading-relaxed text-ink">
              {text ?? "Loading…"}
            </pre>
          )}
        </details>
        <a
          href={artifactUrl(runId, a.name, true)}
          download={a.name}
          aria-label={`Download ${a.name}`}
          className="shrink-0 rounded px-2 py-1 text-xs font-medium text-ink underline underline-offset-2 hover:bg-surface-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          Download
        </a>
      </div>
    </li>
  );
}
