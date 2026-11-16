import { describe, expect, it } from "vitest";
import { CostGuard, MemoryLedger, defaultPolicy, webSearchTool, type FetchLike, type Tool } from "../../src/index.ts";

const KEY = ["sk", "test", "search1234567890"].join("-");

const SONAR_RESPONSE = {
  id: "gen-1",
  model: "perplexity/sonar",
  choices: [
    {
      message: {
        role: "assistant",
        content: "Rust 2024 edition stabilised in Rust 1.85.",
        annotations: [{ type: "url_citation", url_citation: { url: "https://blog.rust-lang.org/a", title: "Announcing Rust 1.85" } }],
      },
    },
  ],
  citations: ["https://blog.rust-lang.org/a", "https://doc.rust-lang.org/edition-guide/"],
  usage: { prompt_tokens: 9, completion_tokens: 12, cost: 0.0061 },
};

function setup(fetchImpl: FetchLike, caps = { taskCap: 1, dayCap: 5 as number | null }) {
  const ledger = new MemoryLedger();
  const guard = new CostGuard(ledger, { ...caps, now: () => Date.parse("2026-11-16T00:00:00Z") });
  const tool = webSearchTool({ fetch: fetchImpl, apiKey: () => KEY, guard });
  return { ledger, guard, tool };
}

const ctx = (callId = "c1") => ({ taskId: "t1", callId, emit: () => {} });
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

async function runTool(tool: Tool, args: unknown, callId?: string): Promise<string> {
  return tool.run(args, ctx(callId));
}

describe("web.search", () => {
  it("returns the answer with numbered citations merged from both response shapes", async () => {
    const { tool } = setup(async () => json(SONAR_RESPONSE));
    const out = await runTool(tool, { query: "when did Rust 2024 land" });
    expect(out).toContain("Rust 2024 edition stabilised");
    expect(out).toContain("Sources:\n1. Announcing Rust 1.85 https://blog.rust-lang.org/a\n2. https://doc.rust-lang.org/edition-guide/");
  });

  it("sends the key only as a bearer header, to the Sonar model, non-streamed", async () => {
    let seen: { url: string; init: RequestInit } | null = null;
    const { tool } = setup(async (url, init) => {
      seen = { url, init };
      return json(SONAR_RESPONSE);
    });
    await runTool(tool, { query: "q" });
    const s = seen as unknown as { url: string; init: RequestInit };
    expect(s.url).toBe("https://openrouter.ai/api/v1/chat/completions");
    expect(new Headers(s.init.headers).get("authorization")).toBe(`Bearer ${KEY}`);
    const body = JSON.parse(String(s.init.body));
    expect(body).toMatchObject({ model: "perplexity/sonar", stream: false });
    expect(JSON.stringify(body)).not.toContain(KEY);
  });

  it("is a network-risk tool, so the default policy asks before the first use", () => {
    const { tool } = setup(async () => json(SONAR_RESPONSE));
    expect(tool.spec.risk).toBe("network");
    expect(defaultPolicy.decide(tool.spec.risk)).toBe("ask");
  });

  it("settles the reserved amount to the provider cost, so the search counts toward the caps", async () => {
    const { tool, ledger } = setup(async () => json(SONAR_RESPONSE));
    await runTool(tool, { query: "q" });
    expect(await ledger.taskSpent("t1")).toBeCloseTo(0.0061, 6);
    expect(await ledger.daySpent("2026-11-16")).toBeCloseTo(0.0061, 6);
  });

  it("does not call the provider when the daily cap has no room", async () => {
    let calls = 0;
    const { tool } = setup(async () => {
      calls += 1;
      return json(SONAR_RESPONSE);
    }, { taskCap: 1, dayCap: 0.001 });
    await expect(runTool(tool, { query: "q" })).rejects.toThrow(/day spend cap/);
    expect(calls).toBe(0);
  });

  it("turns provider errors into plain error observations and releases the reservation", async () => {
    for (const [status, text] of [[401, /rejected/], [402, /no credit/], [429, /rate limiting/], [500, /error \(500\)/]] as const) {
      const { tool, ledger } = setup(async () => json({}, status));
      await expect(runTool(tool, { query: "q" })).rejects.toThrow(text);
      expect(await ledger.taskSpent("t1")).toBe(0);
    }
  });

  it("turns a network failure and an empty answer into error observations", async () => {
    const down = setup(async () => {
      throw new TypeError("boom");
    });
    await expect(runTool(down.tool, { query: "q" })).rejects.toThrow(/could not be reached/);
    const empty = setup(async () => json({ choices: [{ message: { content: "" } }] }));
    await expect(runTool(empty.tool, { query: "q" })).rejects.toThrow(/no answer/);
  });

  it("rejects a missing or oversized query before spending anything", async () => {
    const { tool, ledger } = setup(async () => json(SONAR_RESPONSE));
    await expect(runTool(tool, {})).rejects.toThrow(/"query" must be a string/);
    await expect(runTool(tool, { query: "x".repeat(2001) })).rejects.toThrow(/1 to 2000/);
    expect(await ledger.taskSpent("t1")).toBe(0);
  });

  it("says when no sources came back instead of inventing any", async () => {
    const { tool } = setup(async () => json({ choices: [{ message: { content: "An answer." } }] }));
    expect(await runTool(tool, { query: "q" })).toContain("Sources: none returned.");
  });
});
