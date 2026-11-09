import { useId, useState } from "react";
import { errorMessage, forgetMemory, type MemoryRecall } from "@/lib/api";
import { cn } from "@/lib/cn";

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** "Used N memories" under a reply: opens the list of recalled notes (titles only) with Forget on each. */
export function MemoryChip({ recall, thread }: { recall: MemoryRecall; thread: string }) {
  const [open, setOpen] = useState(false);
  const [gone, setGone] = useState<Set<string>>(() => new Set());
  const [busy, setBusy] = useState("");
  const [failure, setFailure] = useState("");
  const panel = useId();
  const isGone = (id: string, forgotten?: boolean) => forgotten || gone.has(id);
  const used = recall.atoms.length;
  if (used === 0 && recall.withheld === 0) return null;

  async function forget(id: string) {
    setBusy(id);
    setFailure("");
    try {
      await forgetMemory(id, thread);
      setGone((g) => new Set(g).add(id));
    } catch (e) {
      setFailure(errorMessage(e, "Could not forget that note."));
    } finally {
      setBusy("");
    }
  }

  const label = used ? `Used ${plural(used, "memory", "memories")}` : `${plural(recall.withheld, "memory", "memories")} withheld`;
  return (
    <div className="mt-2" data-testid="memory-chip">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panel}
        onClick={() => setOpen((o) => !o)}
        className="inline-flex h-6 items-center gap-1.5 rounded-full border border-line px-2.5 text-xs font-medium text-muted transition-colors hover:bg-surface-2 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-accent" />
        {label}
      </button>
      {open && (
        <div id={panel} className="mt-2 max-w-md rounded-xl border border-line bg-surface-1 p-3 text-sm shadow-card">
          {used > 0 && (
            <ul className="flex flex-col gap-2" aria-label="Recalled memories">
              {recall.atoms.map((a) => (
                <li key={a.id} className="flex items-start justify-between gap-3">
                  <span className={cn("min-w-0", isGone(a.id, a.forgotten) && "text-muted line-through")}>
                    <span className="block truncate text-ink">{a.title || a.id}</span>
                    <span className="mono block text-xs text-muted">
                      {a.id}
                      {a.source ? ` · ${a.source}` : ""}
                    </span>
                  </span>
                  {isGone(a.id, a.forgotten) ? (
                    <span className="shrink-0 text-xs text-muted">Forgotten</span>
                  ) : (
                    <button
                      type="button"
                      onClick={() => forget(a.id)}
                      disabled={busy === a.id}
                      className="shrink-0 rounded-md px-2 py-0.5 text-xs text-muted hover:bg-surface-3 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-50"
                      aria-label={`Forget ${a.title || a.id}`}
                    >
                      Forget
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
          {recall.withheld > 0 && (
            <p className="mt-2 text-xs text-muted">
              {plural(recall.withheld, "note", "notes")} held back by the privacy check and never sent to the model.
            </p>
          )}
          <p className="mt-2 text-xs text-muted">Forget hides a note from this computer's recall. It stays in Kurultai until you delete it there.</p>
          <div aria-live="polite">{failure && <p className="mt-2 text-xs text-urgent">{failure}</p>}</div>
        </div>
      )}
    </div>
  );
}
