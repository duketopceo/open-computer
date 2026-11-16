import { introspectWorkflow } from "cloudflare:test";
import { env } from "./env.ts";
import { beforeEach, describe, expect, it } from "vitest";
import { ProviderError, type AgentEvent, type FetchLike, type StreamPart } from "@open-computer/agent-core";
import { api, install, MODEL_KEY, readSse, seedSecrets, startTask, textTurn, toolTurn, waitFor } from "./support.ts";

beforeEach(async () => {
  await seedSecrets();
});

const session = (id: string) => env.SESSION.get(env.SESSION.idFromName(id));
const eventsUrl = (id: string) => `/v1/sessions/${id}/events`;
const pendingApproval = (id: string) => waitFor(async () => (await session(id).pendingApprovals())[0], "an approval request");
const decide = (sid: string, aid: string, body: object) =>
  api(`/v1/sessions/${sid}/approvals/${aid}`, { method: "POST", body: JSON.stringify(body) });
const resultsOf = (events: AgentEvent[]) => events.filter((e) => e.type === "tool_call.result");

describe("workflow task driver", () => {
  it("completes a two-step task with no approvals and stores all of its events", async () => {
    install([toolTurn([{ id: "c1", name: "sandbox.read_file", args: '{"path":"/workspace/a.txt"}' }]), textTurn("read it")]);
    const { session_id } = await startTask({ goal: "read a file" });
    const { events } = await readSse(await api(eventsUrl(session_id)));
    expect(resultsOf(events)).toMatchObject([{ status: "ok", output: "contents of /workspace/a.txt" }]);
    expect(events.at(-1)).toMatchObject({ type: "memory_write" });
    expect(events.some((e) => e.type === "run" && e.status === "done")).toBe(true);
  });

  it("Covers F2: with no subscriber connected the whole time, the task completes and a later subscriber replays every event", async () => {
    install([toolTurn([{ id: "c1", name: "sandbox.read_file", args: '{"path":"/x"}' }]), textTurn("finished while closed")]);
    const { session_id, task_id } = await startTask({ goal: "work quietly" });
    const state = await waitFor(async () => {
      const s = await session(session_id).taskState(task_id);
      return s && s.status === "done" ? s : undefined;
    }, "the task to finish");
    expect(state.status).toBe("done");
    const { events } = await readSse(await api(eventsUrl(session_id)));
    expect(events.map((e) => e.seq)).toEqual(events.map((_, i) => i));
    expect(events.some((e) => e.type === "message.done" && e.text === "finished while closed")).toBe(true);
  });

  it("pauses at an approval and resumes from the same step when the human approves", async () => {
    const fx = install([toolTurn([{ id: "c1", name: "sandbox.exec", args: '{"command":"ls"}' }]), textTurn("listed")]);
    const { session_id, task_id } = await startTask({ goal: "list files" });
    const pending = await pendingApproval(session_id);
    expect(pending.kind).toBe("tool");
    // The approval row exists a moment before the step saves its state, so wait for the saved state.
    await waitFor(async () => ((await session(session_id).taskState(task_id))?.status === "waiting" ? true : undefined), "the task to be saved as waiting");
    expect(fx.sandboxes.get(task_id)?.execs ?? []).toEqual([]); // nothing ran before approval
    const res = await decide(session_id, pending.id, { hash: pending.hash, approved: true });
    expect(await res.json()).toEqual({ ok: true, outcome: "approved" });
    const { events } = await readSse(await api(eventsUrl(session_id)));
    expect(fx.sandboxes.get(task_id)?.execs).toEqual(["ls"]);
    expect(fx.sandboxes.get(task_id)?.destroyed).toBe(true);
    expect(events.filter((e) => e.type === "approval").map((e) => (e as { state: string }).state)).toEqual(["requested", "approved"]);
    expect(events.some((e) => e.type === "run" && e.status === "done")).toBe(true);
  });

  it("rejects a decision with the wrong hash and leaves the task waiting", async () => {
    install([toolTurn([{ id: "c1", name: "sandbox.exec", args: '{"command":"ls"}' }]), textTurn("x")]);
    const { session_id, task_id } = await startTask({ goal: "list files" });
    const pending = await pendingApproval(session_id);
    const res = await decide(session_id, pending.id, { hash: "not-the-hash", approved: true });
    expect(res.status).toBe(409);
    await waitFor(async () => ((await session(session_id).taskState(task_id))?.status === "waiting" ? true : undefined), "the task to be saved as waiting");
    const again = await decide(session_id, pending.id, { hash: pending.hash, approved: false });
    expect(again.status).toBe(200);
    expect((await decide(session_id, pending.id, { hash: pending.hash, approved: true })).status).toBe(409);
    await readSse(await api(eventsUrl(session_id))); // let the task finish before the test's storage is reset
  });

  it("hands a denial to the model and the task goes on", async () => {
    const fx = install([toolTurn([{ id: "c1", name: "sandbox.exec", args: '{"command":"rm -rf x"}' }]), textTurn("understood")]);
    const { session_id, task_id } = await startTask({ goal: "dangerous" });
    const pending = await pendingApproval(session_id);
    await decide(session_id, pending.id, { hash: pending.hash, approved: false });
    const { events } = await readSse(await api(eventsUrl(session_id)));
    expect(fx.sandboxes.get(task_id)?.execs ?? []).toEqual([]);
    expect(resultsOf(events)).toMatchObject([{ status: "denied" }]);
    expect(events.some((e) => e.type === "run" && e.status === "done")).toBe(true);
  });

  it("ends the task as failed with the expiry reason when the approval wait times out", async () => {
    install([toolTurn([{ id: "c1", name: "sandbox.exec", args: '{"command":"ls"}' }])]);
    const { session_id } = await startTask({ goal: "never approved" });
    const { events } = await readSse(await api(eventsUrl(session_id))); // the test config waits 2 seconds
    expect(events.at(-1)).toMatchObject({ type: "run", status: "failed", reason: expect.stringContaining("expired") });
    expect(events.filter((e) => e.type === "approval").map((e) => (e as { state: string }).state)).toEqual(["requested", "expired"]);
  });

  it("cancelling while waiting ends the instance as terminated and the task as cancelled", async () => {
    install([toolTurn([{ id: "c1", name: "sandbox.exec", args: '{"command":"ls"}' }])]);
    await using_(await introspectWorkflow(env.TASK_WORKFLOW), async (intro) => {
      const { session_id, task_id } = await startTask({ goal: "cancel me" });
      await pendingApproval(session_id);
      const res = await api(`/v1/sessions/${session_id}/tasks/${task_id}/cancel`, { method: "POST" });
      expect(res.status).toBe(200);
      const { events } = await readSse(await api(eventsUrl(session_id)));
      expect(events.at(-1)).toMatchObject({ type: "run", status: "cancelled" });
      const instances = await intro.get();
      await instances[0]?.waitForStatus("terminated");
    });
  });
});

describe("cancel, busy sessions and exhausted retries", () => {
  it("keeps a cancel final when it lands while a step is still waiting on the model", async () => {
    const fx = install([textTurn("this answer arrives after the cancel")]);
    let release!: () => void;
    fx.script.gate = new Promise<void>((resolve) => (release = resolve));
    const { session_id, task_id } = await startTask({ goal: "cancel mid flight" });
    await waitFor(async () => (fx.script.calls > 0 ? true : undefined), "the model call to start");
    expect((await api(`/v1/sessions/${session_id}/tasks/${task_id}/cancel`, { method: "POST" })).status).toBe(200);
    release();
    const { events } = await readSse(await api(eventsUrl(session_id)));
    expect((await session(session_id).taskState(task_id))?.status).toBe("cancelled");
    expect(events.some((e) => e.type === "run" && e.status === "cancelled")).toBe(true);
    expect(events.filter((e) => e.type === "run" && e.status === "done")).toEqual([]);
  });

  it("refuses a second task in a session that still has one running", async () => {
    install([toolTurn([{ id: "c1", name: "sandbox.exec", args: '{"command":"ls"}' }])]);
    const { session_id } = await startTask({ goal: "first" });
    await pendingApproval(session_id);
    const res = await api("/v1/tasks", { method: "POST", body: JSON.stringify({ goal: "second", session_id }) });
    expect(res.status).toBe(409);
    expect(((await res.json()) as { error: { code: string } }).error.code).toBe("session_busy");
    const pending = await pendingApproval(session_id);
    await decide(session_id, pending.id, { hash: pending.hash, approved: false });
    await readSse(await api(eventsUrl(session_id)));
  });

  it("ends a running task with no progress for 15 minutes so the session is not blocked forever, but never one waiting on a person", async () => {
    const fx = install([() => ({ partial: [], throwAfter: new Error("stalled") }), textTurn("the new task works")]);
    let release!: () => void;
    fx.script.gate = new Promise<void>((resolve) => (release = resolve));
    const { session_id, task_id } = await startTask({ goal: "stuck in a step" });
    await waitFor(async () => (fx.script.calls > 0 ? true : undefined), "the model call to start");
    // Just started: busy.
    const busy = await api("/v1/tasks", { method: "POST", body: JSON.stringify({ goal: "second", session_id }) });
    expect(busy.status).toBe(409);
    // 16 minutes of silence later the old task is ended as failed and the new one is accepted.
    fx.setClockSkew(16 * 60 * 1000);
    const next = await api("/v1/tasks", { method: "POST", body: JSON.stringify({ goal: "second", session_id }) });
    expect(next.status).toBe(202);
    const old = await session(session_id).taskState(task_id);
    expect(old?.status).toBe("failed");
    expect(old?.failureReason).toContain("No progress for 15 minutes");
    fx.setClockSkew(0);
    release();
  });

  it("does not end a task that is waiting for an approval, however long it waits", async () => {
    const fx = install([toolTurn([{ id: "c1", name: "sandbox.exec", args: '{"command":"ls"}' }])]);
    const { session_id } = await startTask({ goal: "needs a person" });
    await pendingApproval(session_id);
    fx.setClockSkew(3 * 60 * 60 * 1000);
    const res = await api("/v1/tasks", { method: "POST", body: JSON.stringify({ goal: "second", session_id }) });
    expect(res.status).toBe(409);
    expect(((await res.json()) as { error: { code: string } }).error.code).toBe("session_busy");
    fx.setClockSkew(0);
    const pending = await pendingApproval(session_id);
    await decide(session_id, pending.id, { hash: pending.hash, approved: false });
    await readSse(await api(eventsUrl(session_id)));
  });

  it("ends the task as failed, with its sandbox released, when step retries run out", async () => {
    const fx = install([() => ({ partial: [], throwAfter: new Error("ledger unavailable") })]);
    await using_(await introspectWorkflow(env.TASK_WORKFLOW), async (intro) => {
      await intro.modifyAll(async (m) => {
        await m.disableRetryDelays();
      });
      const { session_id, task_id } = await startTask({ goal: "always failing" });
      const { events } = await readSse(await api(eventsUrl(session_id)));
      expect(events.at(-1)).toMatchObject({ type: "run", status: "failed", reason: expect.stringContaining("repeated errors") });
      expect((await session(session_id).taskState(task_id))?.status).toBe("failed");
      expect(fx.sandboxes.get(task_id)?.destroyed).toBe(true);
    });
  });
});

describe("step retries", () => {
  it("shows a subscriber each delta once when a step fails mid-stream and is retried", async () => {
    const partial: StreamPart[] = [{ t: "text", text: "par" }];
    const fx = install([(call) => (call === 1 ? { partial, throwAfter: new Error("connection reset") } : textTurn("full answer"))]);
    await using_(await introspectWorkflow(env.TASK_WORKFLOW), async (intro) => {
      await intro.modifyAll(async (m) => {
        await m.disableRetryDelays();
      });
      const { session_id } = await startTask({ goal: "flaky" });
      const { events, superseded } = await readSse(await api(eventsUrl(session_id)));
      const deltas = events.filter((e) => e.type === "message.delta").map((e) => (e as { text: string }).text);
      expect(deltas).toEqual(["full answer"]);
      expect(superseded.length).toBeGreaterThanOrEqual(1);
      const seqs = events.map((e) => e.seq);
      expect(seqs).toEqual([...seqs].sort((x, y) => x - y));
      expect(new Set(seqs).size).toBe(seqs.length);
      expect(fx.script.calls).toBe(2);
    });
  });

  it("does not charge twice: a retried step leaves one spend row and one usage audit record for that step", async () => {
    install([(call) => (call === 1 ? { partial: [{ t: "text", text: "p" }], throwAfter: new Error("reset") } : textTurn("ok"))]);
    await using_(await introspectWorkflow(env.TASK_WORKFLOW), async (intro) => {
      await intro.modifyAll(async (m) => {
        await m.disableRetryDelays();
      });
      const { session_id, task_id } = await startTask({ goal: "flaky spend" });
      await readSse(await api(eventsUrl(session_id)));
      const spend = await env.DB.prepare("SELECT COUNT(*) AS n FROM spend WHERE task_id = ?").bind(task_id).first<{ n: number }>();
      expect(spend?.n).toBe(1);
      const usage = await env.DB.prepare("SELECT COUNT(*) AS n FROM audit WHERE task_id = ? AND kind = 'event:usage'").bind(task_id).first<{ n: number }>();
      expect(usage?.n).toBe(1);
    });
  });

  it("ends the task at once, without a retry or a second reservation, on a key-invalid provider error", async () => {
    const fx = install([
      () => {
        throw new ProviderError("key_invalid", "The key was rejected.", 401);
      },
    ]);
    const { session_id, task_id } = await startTask({ goal: "bad key" });
    const { events } = await readSse(await api(eventsUrl(session_id)));
    expect(events.some((e) => e.type === "error" && e.code === "key_invalid")).toBe(true);
    expect(events.at(-1)).toMatchObject({ type: "run", status: "failed" });
    expect(fx.script.calls).toBe(1);
    const spend = await env.DB.prepare("SELECT COUNT(*) AS n FROM spend WHERE task_id = ?").bind(task_id).first<{ n: number }>();
    expect(spend?.n).toBe(1);
  });
});

/** Disposes a workflow introspector after the body, as the plugin requires for test isolation. */
async function using_<T extends { dispose(): Promise<void> }>(resource: T, body: (r: T) => Promise<void>): Promise<void> {
  try {
    await body(resource);
  } finally {
    await resource.dispose();
  }
}

describe("web search through the model key", () => {
  const SONAR = { choices: [{ message: { content: "Answer from the web." } }], citations: ["https://example.test/source"], usage: { cost: 0.006 } };

  it("asks first, then searches with the stored key, returns the sources and counts the cost", async () => {
    install([toolTurn([{ id: "c1", name: "web.search", args: '{"query":"latest rust release"}' }]), textTurn("done")]);
    const seen: Array<{ url: string; auth: string | null; body: string }> = [];
    (globalThis as unknown as { __doubles: { fetch: FetchLike } }).__doubles.fetch = async (url, init) => {
      if (url.endsWith("/models")) return new Response(JSON.stringify({ data: [] }));
      seen.push({ url, auth: new Headers(init.headers).get("authorization"), body: String(init.body) });
      return new Response(JSON.stringify(SONAR));
    };
    const { session_id, task_id } = await startTask({ goal: "look it up" });
    const pending = await pendingApproval(session_id);
    expect(seen).toHaveLength(0); // nothing left the core before approval
    await waitFor(async () => ((await session(session_id).taskState(task_id))?.status === "waiting" ? true : undefined), "the task to be saved as waiting");
    expect((await decide(session_id, pending.id, { hash: pending.hash, approved: true })).status).toBe(200);
    const { events } = await readSse(await api(eventsUrl(session_id)));
    expect(seen).toHaveLength(1);
    expect(seen[0]?.url).toContain("/chat/completions");
    expect(seen[0]?.auth).toBe(`Bearer ${MODEL_KEY}`);
    expect(JSON.parse(seen[0]?.body ?? "{}")).toMatchObject({ model: "perplexity/sonar", stream: false });
    expect(JSON.stringify(events)).toContain("https://example.test/source");
    const spent = await env.DB.prepare("SELECT COALESCE(SUM(amount),0) AS s FROM spend WHERE step_id LIKE 'search:%'").first<{ s: number }>();
    expect(spent?.s).toBeCloseTo(0.006, 6);
  });
});
