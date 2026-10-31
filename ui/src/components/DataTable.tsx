import type { ReactNode } from "react";
import { rel } from "@/lib/format";
import { cn } from "@/lib/cn";

export function Table({ caption, head, children }: { caption: string; head: string[]; children: ReactNode }) {
  return (
    <div
      role="region"
      aria-label={caption}
      tabIndex={0}
      className="overflow-x-auto rounded-2xl border border-line bg-white shadow-card focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal/40"
    >
      <table className="w-full min-w-[34rem] text-left text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr className="border-b border-line text-xs uppercase tracking-wider text-muted">
            {head.map((h) => (
              <th key={h} scope="col" className="px-3 py-2 font-medium">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-line">{children}</tbody>
      </table>
    </div>
  );
}

export function Td({ children, className, mono }: { children: ReactNode; className?: string; mono?: boolean }) {
  return <td className={cn("px-3 py-2 align-top", mono && "mono", className)}>{children}</td>;
}

export function Time({ ts }: { ts: number }) {
  if (!ts) return <span className="text-muted">never</span>;
  const d = new Date(ts * 1000);
  if (Number.isNaN(d.getTime())) return <span className="text-muted">invalid time</span>;
  return (
    <time dateTime={d.toISOString()} title={d.toLocaleString()} className="whitespace-nowrap">
      {rel(ts)}
    </time>
  );
}

const WORDS: Record<string, { glyph: string; cls: string }> = {
  online: { glyph: "●", cls: "text-teal" },
  asleep: { glyph: "◐", cls: "text-amber-700" },
  offline: { glyph: "○", cls: "text-muted" },
  allowed: { glyph: "✓", cls: "text-teal" },
  approved: { glyph: "✓", cls: "text-teal" },
  blocked: { glyph: "✕", cls: "text-red-700" },
  "sent back": { glyph: "↩", cls: "text-amber-700" },
  recorded: { glyph: "•", cls: "text-muted" },
  up: { glyph: "●", cls: "text-teal" },
  down: { glyph: "✕", cls: "text-red-700" },
};

/** State as glyph plus word, never colour alone. */
export function StatusWord({ word }: { word: string }) {
  const w = WORDS[word.toLowerCase()] ?? { glyph: "•", cls: "text-muted" };
  return (
    <span className={cn("inline-flex items-center gap-1.5", w.cls)}>
      <span aria-hidden>{w.glyph}</span>
      {word}
    </span>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-2xl border border-dashed border-line bg-white/60 p-6 text-sm">
      <p className="font-medium text-ink">{title}</p>
      {children && <p className="mt-1 max-w-xl text-muted">{children}</p>}
    </div>
  );
}
