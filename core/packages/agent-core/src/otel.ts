import { sha256Hex } from "./approvals/hash.ts";
import type { AgentEvent } from "./events.ts";
import type { FetchLike } from "./provider/types.ts";

/**
 * OpenTelemetry export (U23): one trace per task, as OTLP/HTTP JSON. A task is the root span, each model step a child,
 * each tool call a child of its step. Only names, statuses, timings, token counts and cost leave the core: prompts,
 * answers, tool arguments and tool output are never exported.
 */
export interface OtlpOptions {
  serviceName?: string;
  serviceVersion?: string;
}

type Attr = { key: string; value: { stringValue: string } | { intValue: string } | { doubleValue: number } | { boolValue: boolean } };

const str = (key: string, v: string): Attr => ({ key, value: { stringValue: v } });
const int = (key: string, v: number): Attr => ({ key, value: { intValue: String(Math.trunc(v)) } });
const dbl = (key: string, v: number): Attr => ({ key, value: { doubleValue: v } });

const nanos = (iso: string): string => (BigInt(Date.parse(iso)) * 1_000_000n).toString();

interface Span {
  traceId: string;
  spanId: string;
  parentSpanId?: string;
  name: string;
  kind: 1;
  startTimeUnixNano: string;
  endTimeUnixNano: string;
  attributes: Attr[];
  status: { code: 0 | 1 | 2; message?: string };
}

async function ids(taskId: string, label: string): Promise<string> {
  return (await sha256Hex(`${taskId}:${label}`)).slice(0, 16);
}

export async function buildOtlpTraces(events: AgentEvent[], options: OtlpOptions = {}): Promise<{ resourceSpans: unknown[] }> {
  const first = events[0];
  if (!first) return { resourceSpans: [] };
  const taskId = first.task_id;
  const traceId = (await sha256Hex(`trace:${taskId}`)).slice(0, 32);
  const rootId = await ids(taskId, "task");
  const last = events[events.length - 1] as AgentEvent;
  const spans: Span[] = [];

  let status: Span["status"] = { code: 0 };
  let costTotal = 0;
  const approvals: Record<string, number> = {};
  const steps = new Map<number, { start: string; end: string; attrs: Attr[] }>();
  const tools = new Map<string, { name: string; risk: string; start: string; end: string; status: string; ms: number }>();

  for (const e of events) {
    switch (e.type) {
      case "run":
        if (e.status === "done") status = { code: 1 };
        else if (e.status === "failed" || e.status === "cancelled") status = { code: 2, message: e.status };
        break;
      case "step": {
        const cur = steps.get(e.step) ?? { start: e.ts, end: e.ts, attrs: [] };
        if (e.state === "start") cur.start = e.ts;
        else cur.end = e.ts;
        steps.set(e.step, cur);
        break;
      }
      case "usage": {
        const cur = steps.get(e.step) ?? { start: e.ts, end: e.ts, attrs: [] };
        cur.attrs.push(int("gen_ai.usage.input_tokens", e.prompt), int("gen_ai.usage.output_tokens", e.completion), int("oc.usage.reasoning_tokens", e.reasoning));
        if (e.cost !== null) {
          cur.attrs.push(dbl("oc.cost_usd", e.cost));
          costTotal += e.cost;
        }
        cur.end = e.ts;
        steps.set(e.step, cur);
        break;
      }
      case "tool_call.finished":
        tools.set(e.call_id, { name: e.name, risk: e.risk, start: e.ts, end: e.ts, status: "pending", ms: 0 });
        break;
      case "tool_call.result": {
        const t = tools.get(e.call_id);
        if (t) {
          t.end = e.ts;
          t.status = e.status;
          t.ms = e.ms;
        }
        break;
      }
      case "approval":
        approvals[e.state] = (approvals[e.state] ?? 0) + 1;
        break;
      default:
        break;
    }
  }

  spans.push({
    traceId,
    spanId: rootId,
    name: "oc.task",
    kind: 1,
    startTimeUnixNano: nanos(first.ts),
    endTimeUnixNano: nanos(last.ts),
    attributes: [str("oc.task_id", taskId), dbl("oc.cost_usd", costTotal), ...Object.entries(approvals).map(([k, v]) => int(`oc.approvals.${k}`, v))],
    status,
  });
  for (const [n, s] of steps) {
    spans.push({
      traceId,
      spanId: await ids(taskId, `step:${n}`),
      parentSpanId: rootId,
      name: "oc.step",
      kind: 1,
      startTimeUnixNano: nanos(s.start),
      endTimeUnixNano: nanos(s.end),
      attributes: [int("oc.step", n), ...s.attrs],
      status: { code: 0 },
    });
  }
  for (const [callId, t] of tools) {
    spans.push({
      traceId,
      spanId: await ids(taskId, `tool:${callId}`),
      parentSpanId: rootId,
      name: `oc.tool ${t.name}`,
      kind: 1,
      startTimeUnixNano: nanos(t.start),
      endTimeUnixNano: nanos(t.end),
      attributes: [str("oc.tool.name", t.name), str("oc.tool.risk", t.risk), str("oc.tool.status", t.status), int("oc.tool.ms", t.ms)],
      status: t.status === "ok" ? { code: 1 } : t.status === "pending" ? { code: 0 } : { code: 2, message: t.status },
    });
  }

  return {
    resourceSpans: [
      {
        resource: { attributes: [str("service.name", options.serviceName ?? "open-computer-core"), str("service.version", options.serviceVersion ?? "0.1.0")] },
        scopeSpans: [{ scope: { name: "open-computer" }, spans }],
      },
    ],
  };
}

/** Sends one task's trace. Best effort: an unreachable collector never affects the task. Returns whether it was accepted. */
export async function exportOtlp(fetchImpl: FetchLike, endpoint: string, events: AgentEvent[], options: OtlpOptions = {}, headers: Record<string, string> = {}): Promise<boolean> {
  try {
    const body = await buildOtlpTraces(events, options);
    if (body.resourceSpans.length === 0) return false;
    const res = await fetchImpl(endpoint, { method: "POST", headers: { "content-type": "application/json", ...headers }, body: JSON.stringify(body), signal: AbortSignal.timeout(5000) });
    return res.ok;
  } catch {
    return false;
  }
}
