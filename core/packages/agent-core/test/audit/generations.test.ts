import { describe, expect, it } from "vitest";
import { openRouterProvider, recentGenerations, type AuditRecord, type StreamPart } from "../../src/index.ts";
import { cassettePlayer } from "../support/cassette.ts";

const rec = (seq: number, task: string | null, data: Record<string, unknown>, kind = "event:usage"): AuditRecord => ({ seq, ts: `t${seq}`, task_id: task, kind, data, prev_hash: "p", hash: "h" });

describe("recent generation ids", () => {
  it("lists newest first, at most N per task, and skips records without an id", () => {
    const records = [
      rec(0, "a", { generation_id: "g1" }),
      rec(1, "a", {}),
      rec(2, "a", { generation_id: "g2" }),
      rec(3, "b", { generation_id: "g3" }),
      rec(4, "a", { generation_id: "g4" }),
      rec(5, "a", { generation_id: "gx" }, "event:cost"),
    ];
    expect(recentGenerations(records, 2, 10).map((g) => g.id)).toEqual(["g4", "g3", "g2"]);
    expect(recentGenerations(records, 5, 2).map((g) => g.id)).toEqual(["g4", "g3"]);
    expect(recentGenerations([])).toEqual([]);
  });

  it("the provider reports the generation id from the stream", async () => {
    const player = cassettePlayer([{ cassette: "text-only" }]);
    const gen = openRouterProvider({ fetch: player.fetch, baseUrl: "https://example.test/api/v1/", apiKey: () => "k" })({ model: "m", messages: [{ role: "user", content: "hi" }] });
    const parts: StreamPart[] = [];
    for await (const p of gen) parts.push(p);
    const done = parts.at(-1);
    expect(done?.t === "done" && done.generationId).toBe("gen-1");
  });
});
