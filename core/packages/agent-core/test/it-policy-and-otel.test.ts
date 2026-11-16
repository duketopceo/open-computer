import { describe, expect, it } from "vitest";
import {
  buildOtlpTraces,
  createPolicy,
  exportOtlp,
  modelRefusal,
  parseItPolicy,
  settingRefusal,
  type AgentEvent,
  type FetchLike,
} from "../src/index.ts";

describe("the IT policy file", () => {
  it("reads every field", () => {
    const p = parseItPolicy(
      JSON.stringify({
        max_daily_cap_usd: 20,
        allowed_models: ["a/m"],
        deny_tools: ["sandbox.exec"],
        risk: { network: "deny", write: "ask" },
        locked_settings: ["daily_cap_usd"],
        otlp_endpoint: "https://otel.example.com/v1/traces",
      }),
    );
    expect(p).toMatchObject({ maxDailyCapUsd: 20, allowedModels: ["a/m"], denyTools: ["sandbox.exec"], risk: { network: "deny", write: "ask" }, lockedSettings: ["daily_cap_usd"] });
    expect(p.otlpEndpoint).toBe("https://otel.example.com/v1/traces");
  });

  it("fails closed on anything it does not understand", () => {
    for (const bad of [
      '{"allowed_model": ["x"]}',
      '{"allowed_models": []}',
      '{"allowed_models": [1]}',
      '{"deny_tools": "x"}',
      '{"risk": {"fly": "allow"}}',
      '{"risk": {"exec": "maybe"}}',
      '{"locked_settings": ["colour"]}',
      '{"otlp_endpoint": "http://plain.example.com"}',
      '{"otlp_endpoint": 5}',
    ]) {
      expect(() => parseItPolicy(bad), bad).toThrow();
    }
  });

  it("refuses models and settings the policy does not allow, with plain words", () => {
    const p = parseItPolicy('{"allowed_models":["a/m"],"locked_settings":["model"]}');
    expect(modelRefusal(p, "a/m")).toBeNull();
    expect(modelRefusal(p, "b/n")).toContain("allows only these models: a/m");
    expect(settingRefusal(p, "model")).toContain("locked");
    expect(settingRefusal(p, "daily_cap_usd")).toBeNull();
    expect(modelRefusal(parseItPolicy(""), "anything")).toBeNull();
  });

  it("turns risk overrides and denied tool names into decisions", () => {
    const p = parseItPolicy('{"risk":{"network":"deny","read":"ask"},"deny_tools":["sandbox.write_file"]}');
    const policy = createPolicy(p.risk, p.denyTools);
    expect(policy.decide("network", "web.search")).toBe("deny");
    expect(policy.decide("read", "sandbox.read_file")).toBe("ask");
    expect(policy.decide("write", "sandbox.write_file")).toBe("deny");
    expect(policy.decide("exec", "sandbox.exec")).toBe("ask");
  });
});

const ev = (seq: number, e: Record<string, unknown>): AgentEvent => ({ task_id: "t1", seq, ts: new Date(Date.UTC(2026, 10, 16, 0, 0, seq)).toISOString(), ...e }) as AgentEvent;

const SECRET_GOAL = "private goal text and secret args";
const EVENTS: AgentEvent[] = [
  ev(0, { type: "run", status: "running" }),
  ev(1, { type: "step", step: 0, state: "start" }),
  ev(2, { type: "message.delta", step: 0, text: SECRET_GOAL }),
  ev(3, { type: "usage", step: 0, prompt: 10, completion: 5, reasoning: 2, cached: 0, cost: 0.002, estimated: false }),
  ev(4, { type: "tool_call.finished", call_id: "c1", name: "sandbox.exec", args: SECRET_GOAL, risk: "exec" }),
  ev(5, { type: "approval", approval_id: "a", kind: "tool", state: "requested", hash: "h", summary: SECRET_GOAL }),
  ev(6, { type: "approval", approval_id: "a", kind: "tool", state: "approved", hash: "h", summary: SECRET_GOAL }),
  ev(7, { type: "tool_call.result", call_id: "c1", status: "ok", output: SECRET_GOAL, ms: 12 }),
  ev(8, { type: "step", step: 0, state: "end" }),
  ev(9, { type: "run", status: "done" }),
];

describe("OpenTelemetry export", () => {
  it("builds one trace with a task span, a step span and a tool span, with cost and token attributes", async () => {
    const t = (await buildOtlpTraces(EVENTS)) as { resourceSpans: Array<{ scopeSpans: Array<{ spans: Array<Record<string, any>> }> }> };
    const spans = t.resourceSpans[0]?.scopeSpans[0]?.spans ?? [];
    expect(spans.map((s) => s.name)).toEqual(["oc.task", "oc.step", "oc.tool sandbox.exec"]);
    const [root, step, tool] = spans as Array<Record<string, any>>;
    expect(new Set(spans.map((s) => s.traceId)).size).toBe(1);
    expect(step?.parentSpanId).toBe(root?.spanId);
    expect(tool?.parentSpanId).toBe(root?.spanId);
    expect(root?.status.code).toBe(1);
    expect(root?.attributes).toEqual(expect.arrayContaining([{ key: "oc.approvals.approved", value: { intValue: "1" } }]));
    expect(step?.attributes).toEqual(expect.arrayContaining([{ key: "gen_ai.usage.input_tokens", value: { intValue: "10" } }, { key: "oc.cost_usd", value: { doubleValue: 0.002 } }]));
    expect(tool?.attributes).toEqual(expect.arrayContaining([{ key: "oc.tool.risk", value: { stringValue: "exec" } }, { key: "oc.tool.status", value: { stringValue: "ok" } }]));
  });

  it("never exports prompts, answers, arguments, outputs or summaries", async () => {
    expect(JSON.stringify(await buildOtlpTraces(EVENTS))).not.toContain("secret");
  });

  it("is deterministic, so a retried export lands on the same ids", async () => {
    expect(JSON.stringify(await buildOtlpTraces(EVENTS))).toBe(JSON.stringify(await buildOtlpTraces(EVENTS)));
  });

  it("marks a failed task as an error and an empty list as nothing to send", async () => {
    const failed = (await buildOtlpTraces([ev(0, { type: "run", status: "running" }), ev(1, { type: "run", status: "failed", reason: "x" })])) as any;
    expect(failed.resourceSpans[0].scopeSpans[0].spans[0].status.code).toBe(2);
    expect((await buildOtlpTraces([])).resourceSpans).toEqual([]);
  });

  it("posts JSON to the endpoint and reports failure without throwing", async () => {
    let seen: { url: string; ct: string | null } | null = null;
    const ok: FetchLike = async (url, init) => ((seen = { url, ct: new Headers(init.headers).get("content-type") }), new Response("{}"));
    expect(await exportOtlp(ok, "https://c.example/v1/traces", EVENTS)).toBe(true);
    expect(seen).toEqual({ url: "https://c.example/v1/traces", ct: "application/json" });
    expect(await exportOtlp(async () => new Response("no", { status: 500 }), "https://c.example", EVENTS)).toBe(false);
    expect(await exportOtlp(async () => { throw new TypeError("down"); }, "https://c.example", EVENTS)).toBe(false);
  });
});
