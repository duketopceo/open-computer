import { describe, expect, it } from "vitest";
import { openRouterProvider, ProviderError, type FetchLike, type ProviderRequest, type StreamPart } from "../../src/index.ts";
import { cassettePlayer } from "../support/cassette.ts";
import { fakeGenericKey } from "../support/fakes.ts";

const KEY = fakeGenericKey();

function make(player: ReturnType<typeof cassettePlayer>) {
  return openRouterProvider({ fetch: player.fetch, baseUrl: "https://example.test/api/v1/", apiKey: () => KEY });
}

const baseRequest: ProviderRequest = { model: "example/model", messages: [{ role: "user", content: "hi" }] };

async function collect(gen: AsyncGenerator<StreamPart>): Promise<StreamPart[]> {
  const out: StreamPart[] = [];
  for await (const p of gen) out.push(p);
  return out;
}

describe("openrouter provider (cassettes)", () => {
  it("streams text deltas in order, then one done part with cost", async () => {
    const parts = await collect(make(cassettePlayer([{ cassette: "text-only" }]))(baseRequest));
    expect(parts.filter((p) => p.t === "text").map((p) => (p as { text: string }).text)).toEqual(["Hello", ", world"]);
    const done = parts.at(-1);
    expect(done?.t).toBe("done");
    if (done?.t !== "done") throw new Error("no done");
    expect(done.message.content).toBe("Hello, world");
    expect(done.finishReason).toBe("stop");
    expect(done.usage).toEqual({ prompt: 12, completion: 4, reasoning: 0, cached: 2, cost: 0.00021 });
  });

  it("yields plain reasoning deltas as text-kind reasoning and reports reasoning tokens", async () => {
    const parts = await collect(make(cassettePlayer([{ cassette: "reasoning-plain" }]))(baseRequest));
    const r = parts.filter((p) => p.t === "reasoning") as Array<{ kind: string; text: string }>;
    expect(r).toEqual([
      { t: "reasoning", kind: "text", text: "Let me think. " },
      { t: "reasoning", kind: "text", text: "Two plus two." },
    ]);
    const done = parts.at(-1);
    if (done?.t !== "done") throw new Error("no done");
    expect(done.usage?.reasoning).toBe(25);
    expect(done.reasoningKinds).toEqual(["text"]);
  });

  it("maps reasoning_details text and summary kinds, and keeps encrypted blocks for replay only", async () => {
    const parts = await collect(make(cassettePlayer([{ cassette: "reasoning-details" }]))(baseRequest));
    const r = parts.filter((p) => p.t === "reasoning") as Array<{ kind: string; text: string }>;
    // The plain `reasoning` string in the first chunk duplicates the summary and must not double-emit.
    expect(r.map((x) => [x.kind, x.text])).toEqual([
      ["summary", "Plan: "],
      ["summary", "check files."],
      ["text", "Raw thought A. "],
    ]);
    expect(JSON.stringify(parts.filter((p) => p.t !== "done"))).not.toContain("ENCRYPTED-BLOCK");
    const done = parts.at(-1);
    if (done?.t !== "done" || done.message.role !== "assistant") throw new Error("no done");
    expect(done.message.reasoning_details).toEqual([
      { type: "reasoning.summary", summary: "Plan: check files.", index: 0 },
      { type: "reasoning.text", text: "Raw thought A. ", index: 1, signature: "sig-1" },
      { type: "reasoning.encrypted", data: "ENCRYPTED-BLOCK", index: 2 },
    ]);
  });

  it("assembles two parallel tool calls with interleaved argument deltas in index order", async () => {
    const parts = await collect(make(cassettePlayer([{ cassette: "tool-parallel" }]))(baseRequest));
    const starts = parts.filter((p) => p.t === "tool_start");
    expect(starts).toEqual([
      { t: "tool_start", index: 0, call_id: "call_a", name: "sandbox.exec" },
      { t: "tool_start", index: 1, call_id: "call_b", name: "sandbox.read_file" },
    ]);
    const done = parts.at(-1);
    if (done?.t !== "done" || done.message.role !== "assistant") throw new Error("no done");
    expect(done.finishReason).toBe("tool_calls");
    expect(done.message.content).toBeNull();
    expect(done.message.tool_calls).toEqual([
      { id: "call_a", name: "sandbox.exec", arguments: '{"cmd":"ls"}' },
      { id: "call_b", name: "sandbox.read_file", arguments: '{"path":"a.txt"}' },
    ]);
  });

  it("ignores comment lines and the done marker without error", async () => {
    const parts = await collect(make(cassettePlayer([{ cassette: "text-only" }], 1))(baseRequest));
    expect(parts.some((p) => p.t === "error")).toBe(false);
    expect(parts.at(-1)?.t).toBe("done");
  });

  it("yields partial text, then an error part, when the stream ends with an error chunk", async () => {
    const parts = await collect(make(cassettePlayer([{ cassette: "mid-stream-error" }]))(baseRequest));
    expect(parts.map((p) => p.t)).toEqual(["text", "error"]);
    const err = parts[1];
    if (err?.t !== "error") throw new Error("no error");
    expect(err.message).toContain("Upstream provider dropped the connection");
  });

  it("reports a response cut off by the output limit as an error, never as a finished answer", async () => {
    const parts = await collect(make(cassettePlayer([{ cassette: "truncated-length" }]))(baseRequest));
    expect(parts.map((p) => p.t)).toEqual(["text", "error"]);
    expect(parts[1]).toMatchObject({ t: "error", message: expect.stringContaining("cut off") });
  });

  it("reports a stream that ends with no finish reason as an error, never as a finished answer", async () => {
    const parts = await collect(make(cassettePlayer([{ cassette: "early-eof" }]))(baseRequest));
    expect(parts.map((p) => p.t)).toEqual(["text", "error"]);
    expect(parts.some((p) => p.t === "done")).toBe(false);
  });

  it("stops reading and yields nothing more when the signal aborts mid-stream", async () => {
    const ac = new AbortController();
    const player = cassettePlayer([{ cassette: "text-only", hangAfterChunks: 14 }]);
    const seen: StreamPart[] = [];
    for await (const p of make(player)(baseRequest, ac.signal)) {
      seen.push(p);
      ac.abort();
    }
    expect(seen.some((p) => p.t === "done")).toBe(false);
  });

  it("stops a stream that goes silent and reports a retryable timeout instead of hanging", async () => {
    const player = cassettePlayer([{ cassette: "text-only", hangAfterChunks: 14 }]);
    const provider = openRouterProvider({ fetch: player.fetch, baseUrl: "https://example.test/api/v1", apiKey: () => KEY, timeouts: { idleMs: 40 } });
    const started = Date.now();
    const parts = await collect(provider(baseRequest));
    expect(Date.now() - started).toBeLessThan(2000);
    expect(parts.at(-1)).toMatchObject({ t: "error", code: "provider_timeout", retryable: true, message: expect.stringContaining("stopped sending tokens") });
    expect(parts.some((p) => p.t === "done")).toBe(false);
  });

  it("counts keep-alive comment bytes as activity, so a slow but live stream is not cut off", async () => {
    // Chunks arrive every ~15 ms against a 100 ms idle limit.
    const slowFetch: FetchLike = async () => {
      const encoder = new TextEncoder();
      const lines = [": OPENROUTER PROCESSING\n\n", ": OPENROUTER PROCESSING\n\n", 'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\n', "data: [DONE]\n\n"];
      let i = 0;
      return new Response(
        new ReadableStream({
          async pull(controller) {
            if (i >= lines.length) return controller.close();
            await new Promise((r) => setTimeout(r, 15));
            controller.enqueue(encoder.encode(lines[i++]));
          },
        }),
      );
    };
    const provider = openRouterProvider({ fetch: slowFetch, baseUrl: "https://example.test/api/v1", apiKey: () => KEY, timeouts: { idleMs: 100 } });
    const parts = await collect(provider(baseRequest));
    expect(parts.at(-1)?.t).toBe("done");
  });

  it("reports a response that never starts as a retryable timeout", async () => {
    const neverStarts: FetchLike = (_url, init) =>
      new Promise((_resolve, reject) => init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError"))));
    const provider = openRouterProvider({ fetch: neverStarts, baseUrl: "https://example.test/api/v1", apiKey: () => KEY, timeouts: { connectMs: 30 } });
    const parts = await collect(provider(baseRequest));
    expect(parts).toEqual([{ t: "error", code: "provider_timeout", message: "The model did not start answering in time.", retryable: true }]);
  });

  it("stops a call that runs past the total limit even while bytes keep arriving", async () => {
    const endless: FetchLike = async (_url, init) =>
      new Response(
        new ReadableStream({
          async pull(controller) {
            await new Promise((r) => setTimeout(r, 10));
            if (init.signal?.aborted) return controller.error(new DOMException("aborted", "AbortError"));
            controller.enqueue(new TextEncoder().encode(": still working\n\n"));
          },
        }),
      );
    const provider = openRouterProvider({ fetch: endless, baseUrl: "https://example.test/api/v1", apiKey: () => KEY, timeouts: { totalMs: 60, idleMs: 1000 } });
    const parts = await collect(provider(baseRequest));
    expect(parts.at(-1)).toMatchObject({ t: "error", code: "provider_timeout", message: expect.stringContaining("took too long") });
  });

  it("does not turn a caller's own cancel into a timeout", async () => {
    const ac = new AbortController();
    const player = cassettePlayer([{ cassette: "text-only", hangAfterChunks: 14 }]);
    const provider = openRouterProvider({ fetch: player.fetch, baseUrl: "https://example.test/api/v1", apiKey: () => KEY, timeouts: { idleMs: 5000 } });
    const seen: StreamPart[] = [];
    for await (const p of provider(baseRequest, ac.signal)) {
      seen.push(p);
      ac.abort();
    }
    expect(seen.some((p) => p.t === "error")).toBe(false);
  });

  it("sends stream, reasoning, tools and require_parameters, and never puts the key in the body", async () => {
    const player = cassettePlayer([{ cassette: "text-only" }]);
    await collect(
      make(player)({
        ...baseRequest,
        reasoning: { effort: "low" },
        tools: [{ name: "sandbox.exec", description: "run", parameters: { type: "object" }, risk: "exec" }],
      }),
    );
    const call = player.calls[0];
    if (!call) throw new Error("no call");
    expect(call.url).toBe("https://example.test/api/v1/chat/completions");
    expect(call.body.stream).toBe(true);
    expect(call.body.reasoning).toEqual({ effort: "low" });
    expect(call.body.provider).toEqual({ require_parameters: true });
    expect((call.body.tools as unknown[]).length).toBe(1);
    expect(JSON.stringify(call.body)).not.toContain(KEY);
    expect((call.init.headers as Record<string, string>).authorization).toBe(`Bearer ${KEY}`);
  });

  it("replays assistant tool calls and reasoning_details on the wire in OpenAI shape", async () => {
    const player = cassettePlayer([{ cassette: "text-only" }]);
    await collect(
      make(player)({
        model: "example/model",
        messages: [
          { role: "user", content: "go" },
          {
            role: "assistant",
            content: null,
            tool_calls: [{ id: "call_a", name: "sandbox.exec", arguments: "{}" }],
            reasoning_details: [{ type: "reasoning.encrypted", data: "X", index: 0 }],
          },
          { role: "tool", tool_call_id: "call_a", content: "ok" },
        ],
      }),
    );
    const messages = player.calls[0]?.body.messages as Array<Record<string, unknown>>;
    expect(messages[1]).toEqual({
      role: "assistant",
      content: null,
      tool_calls: [{ id: "call_a", type: "function", function: { name: "sandbox.exec", arguments: "{}" } }],
      reasoning_details: [{ type: "reasoning.encrypted", data: "X", index: 0 }],
    });
  });

  it("maps 401 to key_invalid without leaking the key, even when the body echoes it", async () => {
    const player = cassettePlayer([{ status: 401, text: `{"error":"bad key ${KEY}"}` }]);
    const err = await collect(make(player)(baseRequest)).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ProviderError);
    const pe = err as ProviderError;
    expect(pe.code).toBe("key_invalid");
    expect(pe.message).not.toContain(KEY);
  });

  it("maps 402 and 429 to typed errors", async () => {
    for (const [status, code] of [[402, "payment_required"], [429, "rate_limited"]] as const) {
      const err = await collect(make(cassettePlayer([{ status, text: "x" }]))(baseRequest)).catch((e: unknown) => e);
      expect((err as ProviderError).code).toBe(code);
    }
  });
});
