import { Icon } from "./icons/Icon";
import { useId, useState, type ReactNode } from "react";
import type { EffortCatalog, GoalOptions } from "@/lib/api";
import { cn } from "@/lib/cn";

export interface EffortChoice {
  effort: string;
  model: string; // "" = the effort level's own model
  budget: string; // "" = the effort level's own budget
}

/** The one place a budget typed in Advanced is checked; null means it is invalid. */
export function toOptions(c: EffortChoice, cat: EffortCatalog | null): GoalOptions | null {
  if (!cat) return {};
  const opts: GoalOptions = { effort: c.effort };
  if (c.model) opts.model = c.model;
  const t = c.budget.trim();
  if (t) {
    const n = Number(t);
    if (!isFinite(n) || n <= 0 || n > cat.max_budget) return null;
    opts.budget = n;
  }
  return opts;
}

/** Dollars with cents, and up to four places when the amount needs them ($0.10, $0.001). */
const usd = (n: number) => `$${n.toFixed(4).replace(/0{1,2}$/, "")}`;

export function EffortPicker({
  catalog,
  value,
  onChange,
  disabled,
  lead,
}: {
  lead?: ReactNode; // sits at the start of the picker row (the composer's dictate button)
  catalog: EffortCatalog;
  value: EffortChoice;
  onChange: (c: EffortChoice) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const uid = useId();
  const level = catalog.levels.find((l) => l.id === value.effort) ?? catalog.levels[0];
  const budgetBad = toOptions(value, catalog) === null;
  const advanced = !!(value.model || value.budget.trim());

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-2">
        {lead}
        <fieldset className="m-0 min-w-0 border-0 p-0" disabled={disabled}>
          <legend className="sr-only">Effort</legend>
          <div className="flex flex-wrap items-center gap-1 rounded-2xl border border-line bg-canvas p-0.5">
            {catalog.levels.map((l) => (
              <label key={l.id} className="relative" title={l.blurb || undefined}>
                <input
                  type="radio"
                  name={`${uid}-effort`}
                  value={l.id}
                  checked={l.id === level.id}
                  onChange={() => onChange({ ...value, effort: l.id })}
                  className="peer sr-only"
                />
                <span
                  className={cn(
                    "inline-flex h-7 max-md:h-10 cursor-pointer items-center gap-1.5 rounded-full px-2.5 text-xs font-medium text-muted transition-colors",
                    "hover:text-ink peer-checked:bg-accent-soft peer-checked:text-accent peer-checked:ring-1 peer-checked:ring-accent",
                    "peer-focus-visible:ring-2 peer-focus-visible:ring-accent peer-disabled:cursor-not-allowed peer-disabled:opacity-50",
                  )}
                >
                  <span aria-hidden className="text-xs">
                    {l.id === level.id ? "●" : "○"}
                  </span>
                  {l.label}
                </span>
              </label>
            ))}
          </div>
        </fieldset>
        <span className={cn("mono text-xs", budgetBad ? "text-urgent" : "text-muted")} aria-live="polite">
          {budgetBad
            ? `budget must be above $0 and up to ${usd(catalog.max_budget)}`
            : `up to ${usd(Number(value.budget.trim()) > 0 ? Number(value.budget) : level.budget)} per run`}
        </span>
        <button
          type="button"
          aria-expanded={open}
          aria-controls={`${uid}-adv`}
          onClick={() => setOpen((o) => !o)}
          className="inline-flex h-7 max-md:h-10 items-center gap-1 rounded-full px-2 text-xs font-medium text-muted hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          <Icon name={open ? "collapse" : "expand"} />
          Advanced
          {advanced && !open && <span className="mono text-accent"> · changed</span>}
        </button>
      </div>


      {open && (
        <div id={`${uid}-adv`} className="grid gap-3 rounded-xl border border-line bg-canvas p-3 sm:grid-cols-2">
          <div>
            <label htmlFor={`${uid}-model`} className="block text-xs text-muted">
              Model
            </label>
            <select
              id={`${uid}-model`}
              value={value.model}
              disabled={disabled}
              onChange={(e) => onChange({ ...value, model: e.target.value })}
              className="mono mt-0.5 h-9 w-full rounded-lg border border-line bg-surface-1 px-2 text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              <option value="">Automatic ({level.model})</option>
              {catalog.models.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
            <p className="mt-1 text-xs text-muted">
              {level.blurb} Uses <span className="mono">{value.model || level.model}</span>
              {!value.model && level.fallbacks?.length ? (
                <>
                  , then <span className="mono">{level.fallbacks.join(", ")}</span>
                </>
              ) : null}
              .
            </p>
          </div>
          <div>
            <label htmlFor={`${uid}-budget`} className="block text-xs text-muted">
              Budget for this run, dollars
            </label>
            <input
              id={`${uid}-budget`}
              inputMode="decimal"
              value={value.budget}
              disabled={disabled}
              onChange={(e) => onChange({ ...value, budget: e.target.value })}
              placeholder={level.budget.toFixed(2)}
              aria-invalid={budgetBad}
              aria-describedby={`${uid}-budget-help`}
              className="mono mt-0.5 h-9 w-full rounded-lg border border-line bg-surface-1 px-3 text-ink placeholder:text-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            />
            <p id={`${uid}-budget-help`} className={cn("mt-1 text-xs", budgetBad ? "text-urgent" : "text-muted")} role={budgetBad ? "alert" : undefined}>
              {budgetBad
                ? `Enter an amount above 0 and up to ${usd(catalog.max_budget)}.`
                : `The run pauses at this amount. Most you can set: ${usd(catalog.max_budget)} (the daily cap).`}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
