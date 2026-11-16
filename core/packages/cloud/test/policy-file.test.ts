import { beforeEach, describe, expect, it } from "vitest";
import type { FetchLike } from "@open-computer/agent-core";
import type { Doubles } from "./entry.ts";
import { api, install, readSse, seedSecrets, startTask, textTurn, toolTurn } from "./support.ts";

beforeEach(async () => {
  await seedSecrets();
});

const doubles = (): Doubles => (globalThis as unknown as { __doubles: Doubles }).__doubles;

describe("the policy file on the cloud core", () => {
  it("refuses a model outside allowed_models with a plain 403, and allows the listed one", async () => {
    const fx = install([textTurn("ok")]);
    fx.setPolicy('{"allowed_models":["z-ai/glm-5.3"]}');
    const res = await api("/v1/tasks", { method: "POST", body: JSON.stringify({ goal: "g", model: "other/model" }) });
    expect(res.status).toBe(403);
    expect(((await res.json()) as { error: { code: string; message: string } }).error).toMatchObject({ code: "policy_model" });
    const ok = await api("/v1/tasks", { method: "POST", body: JSON.stringify({ goal: "g", model: "z-ai/glm-5.3" }) });
    expect(ok.status).toBe(202);
    // The default model is allowed too, so a plain task still works.
    await readSse(await api(`/v1/sessions/${((await ok.json()) as { session_id: string }).session_id}/events`));
  });

  it("refuses to change a locked setting", async () => {
    const fx = install([textTurn("ok")]);
    fx.setPolicy('{"locked_settings":["daily_cap_usd"]}');
    const res = await api("/v1/settings", { method: "PUT", body: JSON.stringify({ daily_cap_usd: 2 }) });
    expect(res.status).toBe(403);
    expect(((await res.json()) as { error: { code: string } }).error.code).toBe("policy_locked");
  });

  it("denies a tool named in deny_tools without asking, and hands the denial to the model", async () => {
    const fx = install([toolTurn([{ id: "c1", name: "sandbox.exec", args: '{"command":"ls"}' }]), textTurn("understood")]);
    fx.setPolicy('{"deny_tools":["sandbox.exec"]}');
    const { session_id } = await startTask({ goal: "list files" });
    const { events } = await readSse(await api(`/v1/sessions/${session_id}/events`));
    expect(events.some((e) => e.type === "approval")).toBe(false);
    expect(events.some((e) => e.type === "tool_call.result" && e.status === "denied")).toBe(true);
    expect(fx.sandboxes.size === 0 || [...fx.sandboxes.values()].every((b) => b.execs.length === 0)).toBe(true);
  });

  it("sends a trace without prompts to the policy's OTLP endpoint when a task ends", async () => {
    const fx = install([textTurn("the secret answer")]);
    fx.setPolicy('{"otlp_endpoint":"https://otel.example.com/v1/traces"}');
    const posts: string[] = [];
    const inner: FetchLike = doubles().fetch;
    doubles().fetch = async (url, init) => {
      if (url.startsWith("https://otel.example.com")) {
        posts.push(String(init.body));
        return new Response("{}");
      }
      return inner(url, init);
    };
    const { session_id } = await startTask({ goal: "a private goal" });
    await readSse(await api(`/v1/sessions/${session_id}/events`));
    for (let i = 0; i < 50 && posts.length === 0; i++) await new Promise((r) => setTimeout(r, 20));
    expect(posts).toHaveLength(1);
    expect(posts[0]).toContain("oc.task");
    expect(posts[0]).not.toContain("secret answer");
    expect(posts[0]).not.toContain("private goal");
  });
});
