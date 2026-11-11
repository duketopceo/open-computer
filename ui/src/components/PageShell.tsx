import type { ReactNode } from "react";
import { AppShell } from "./AppShell";

/** Chrome for every React page except `/` (which keeps its own task-focused body). */
export function PageShell({ title, sub, children }: { title: string; sub?: string; children: ReactNode }) {
  return (
    <AppShell title={title} mainClassName="px-4 py-6 md:px-8 md:py-8">
      <div className="mx-auto w-full max-w-5xl">
        <h1 className="font-display text-2xl text-ink">{title}</h1>
        {sub ? <p className="mt-1 mb-6 max-w-2xl text-sm text-muted">{sub}</p> : <div className="mb-6" />}
        {children}
      </div>
    </AppShell>
  );
}
