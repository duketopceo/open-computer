import type { ReactNode } from "react";
import { rel } from "@/lib/format";
import { Icon, type IconName } from "./icons/Icon";
import { EmptyState } from "./EmptyState";
import { cn } from "@/lib/cn";

export function Table({ caption, head, children }: { caption: string; head: string[]; children: ReactNode }) {
  return (
    <div
      role="region"
      aria-label={caption}
      tabIndex={0}
      className="overflow-x-auto rounded-2xl border border-line bg-surface-1 shadow-card focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
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

const WORDS: Record<string, { glyph: IconName; cls: string }> = {
  online: { glyph: "done", cls: "text-accent" },
  asleep: { glyph: "leased", cls: "text-attention" },
  offline: { glyph: "queued", cls: "text-muted" },
  allowed: { glyph: "done", cls: "text-accent" },
  approved: { glyph: "done", cls: "text-accent" },
  blocked: { glyph: "denied", cls: "text-urgent" },
  "sent back": { glyph: "retry", cls: "text-attention" },
  recorded: { glyph: "step", cls: "text-muted" },
  up: { glyph: "done", cls: "text-accent" },
  down: { glyph: "denied", cls: "text-urgent" },
};

/** State as glyph plus word, never colour alone. */
export function StatusWord({ word }: { word: string }) {
  const w = WORDS[word.toLowerCase()] ?? { glyph: "step" as IconName, cls: "text-muted" };
  return (
    <span className={cn("inline-flex items-center gap-1.5", w.cls)}>
      <Icon name={w.glyph} />
      {word}
    </span>
  );
}

export function Empty({ title, children, icon = "step" }: { title: string; children?: ReactNode; icon?: IconName }) {
  return <EmptyState icon={icon} title={title}>{children}</EmptyState>;
}
