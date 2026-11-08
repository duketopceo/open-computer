import { useState } from "react";
import { decidePlan, errorMessage, type PlanStep, type RunPlan } from "@/lib/api";
import { cost } from "@/lib/format";
import { Button } from "./primitives";

const TIERS = ["cheap", "mid", "frontier"] as const;

/** The plan a run is holding for review: edit step text and tier, drop steps, approve or cancel. Nothing has run yet. */
export function PlanCard({ runId, plan, onDecided }: { runId: string; plan: RunPlan; onDecided: () => void }) {
  const [steps, setSteps] = useState<PlanStep[]>(plan.steps);
  const [removed, setRemoved] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const edit = (id: string, patch: Partial<PlanStep>) =>
    setSteps((all) => all.map((s) => (s.id === id ? { ...s, ...patch } : s)));
  const toggle = (id: string) =>
    setRemoved((r) => {
      const n = new Set(r);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  const edits = () => {
    const out: Record<string, unknown>[] = [];
    for (const s of steps) {
      const orig = plan.steps.find((o) => o.id === s.id);
      if (!orig) continue;
      if (removed.has(s.id)) out.push({ id: s.id, remove: true });
      else if (s.title !== orig.title || s.prompt !== orig.prompt || s.tier !== orig.tier)
        out.push({ id: s.id, title: s.title, prompt: s.prompt, tier: s.tier });
    }
    return out;
  };

  const send = async (action: "approve" | "cancel") => {
    setBusy(true);
    setError("");
    try {
      await decidePlan(runId, action, action === "approve" ? edits() : []);
      onDecided();
    } catch (e) {
      setError(errorMessage(e, "Could not save your decision."));
      setBusy(false);
    }
  };

  const kept = steps.length - removed.size;
  return (
    <section
      aria-label="Plan to review"
      className="mb-6 rounded-2xl border border-line bg-surface-1 shadow-card"
      data-testid="plan-card"
    >
      <header className="border-b border-line px-4 py-3">
        <p className="mono uppercase tracking-wider text-accent">Plan · nothing has run yet</p>
        <p className="mt-1 text-sm text-ink-muted">
          {kept} step{kept === 1 ? "" : "s"}
          {plan.model ? ` · ${plan.model}` : ""} · estimate {cost(plan.estimate)} (a guess
          {plan.budget != null ? `; hard cap ${cost(plan.budget)}` : ""})
        </p>
      </header>
      <ol className="divide-y divide-line">
        {steps.map((s, i) => {
          const gone = removed.has(s.id);
          return (
            <li key={s.id} className={gone ? "px-4 py-3 opacity-50" : "px-4 py-3"}>
              <div className="flex items-start gap-3">
                <span className="mono mt-2 text-muted">{i + 1}</span>
                <div className="min-w-0 flex-1 space-y-2">
                  <input
                    aria-label={`Step ${i + 1} title`}
                    value={s.title}
                    disabled={gone}
                    onChange={(e) => edit(s.id, { title: e.target.value })}
                    className="w-full rounded-lg border border-line bg-canvas px-2 py-1.5 text-sm font-medium text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                  />
                  <textarea
                    aria-label={`Step ${i + 1} instructions`}
                    value={s.prompt}
                    disabled={gone}
                    rows={2}
                    onChange={(e) => edit(s.id, { prompt: e.target.value })}
                    className="w-full resize-y rounded-lg border border-line bg-canvas px-2 py-1.5 text-xs text-ink-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                  />
                  <div className="flex flex-wrap items-center gap-3 text-xs text-muted">
                    <label className="flex items-center gap-1.5">
                      model tier
                      <select
                        value={s.tier ?? "mid"}
                        disabled={gone}
                        onChange={(e) => edit(s.id, { tier: e.target.value })}
                        className="rounded-md border border-line bg-canvas px-1.5 py-0.5 text-ink"
                      >
                        {TIERS.map((t) => (
                          <option key={t}>{t}</option>
                        ))}
                      </select>
                    </label>
                    {(s.deps?.length ?? 0) > 0 && <span className="mono">after {s.deps!.join(", ")}</span>}
                    {s.needs_egress && <span>needs network: {(s.egress_hosts ?? []).join(", ") || "approval"}</span>}
                    <span className="mono">{cost(s.est_cost ?? 0)}</span>
                    <button
                      type="button"
                      onClick={() => toggle(s.id)}
                      className="ml-auto rounded px-1 text-muted underline underline-offset-2 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                    >
                      {gone ? "Keep step" : "Remove step"}
                    </button>
                  </div>
                </div>
              </div>
            </li>
          );
        })}
      </ol>
      <footer className="flex flex-wrap items-center justify-end gap-2 border-t border-line px-4 py-3">
        {error && (
          <p role="alert" className="mr-auto text-xs text-urgent">
            {error}
          </p>
        )}
        <Button variant="subtle" disabled={busy} onClick={() => send("cancel")}>
          Cancel run
        </Button>
        <Button variant="primary" disabled={busy || kept === 0} onClick={() => send("approve")}>
          {edits().length ? "Approve with edits" : "Approve and run"}
        </Button>
      </footer>
    </section>
  );
}
