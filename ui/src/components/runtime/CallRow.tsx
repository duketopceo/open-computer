import { cost } from "@/lib/format";
import type { LlmCallData } from "@/lib/runEvents";

export const kTokens = (n?: number) => (n == null ? "—" : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n));
export const ms = (n?: number) => (n == null ? "" : n < 1000 ? `${Math.round(n)} ms` : `${(n / 1000).toFixed(1)} s`);

const KIND: Record<string, string> = {
  thread: "Chat call",
  chat: "Chat call", plan: "Planning call", work: "Work call", decision: "Routing decision call" };

/** `raw` (dev mode) adds the plan JSON and the model's reasoning; everyone else sees model, tokens and cost. */
export function CallRow({ call, raw = false }: { call: LlmCallData; raw?: boolean }) {
  const served = call.model_served || call.model_requested || "unknown model";
  const differs = call.model_requested && call.model_served && call.model_requested !== call.model_served;
  return (
    <div className="rounded-lg border border-line-faint bg-canvas px-3 py-2 text-xs">
      <p className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="font-medium text-ink">{KIND[call.kind] ?? call.kind}</span>
        <span className="mono text-ink-muted">{served}</span>
        {call.provider && <span className="mono text-muted">via {call.provider}</span>}
        <span className="mono ml-auto text-muted">{cost(call.cost_usd)}</span>
      </p>
      <p className="mono mt-1 text-muted">
        in {kTokens(call.tokens_in)} · out {kTokens(call.tokens_out)} · reasoning {kTokens(call.tokens_reasoning)}
        {call.latency_ms != null && ` · ${ms(call.latency_ms)}`}
        {differs && ` · requested ${call.model_requested}`}
      </p>
      {call.status === "error" ? (
        <p className="mt-2 text-urgent">
          Call failed{call.http_status ? ` (HTTP ${call.http_status})` : ""}
          {call.error_class ? `: ${call.error_class}` : ""}
          {call.error ? ` — ${call.error}` : ""}
        </p>
      ) : !raw ? null : call.detail ? (
        <details className="mt-2">
          <summary className="cursor-pointer select-none text-muted hover:text-ink">Plan</summary>
          <pre tabIndex={0} aria-label="Plan" className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap rounded-lg bg-surface-1 p-3 leading-relaxed text-ink-muted">
            {call.detail}
          </pre>
        </details>
      ) : call.reasoning ? (
        <details className="mt-2">
          <summary className="cursor-pointer select-none text-muted hover:text-ink">Reasoning</summary>
          <pre
            tabIndex={0}
            aria-label="Model reasoning"
            className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap rounded-lg bg-surface-1 p-3 leading-relaxed text-ink-muted"
          >
            {call.reasoning}
          </pre>
        </details>
      ) : call.kind === "plan" ? null : (
        <p className="mt-2 text-muted">no reasoning returned</p>
      )}
    </div>
  );
}
