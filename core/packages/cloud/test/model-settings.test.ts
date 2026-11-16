import { beforeEach, describe, expect, it } from "vitest";
import { env } from "./env.ts";
import { api, install, readSse, seedSecrets, startTask, textTurn } from "./support.ts";

beforeEach(async () => {
  await seedSecrets();
  await env.DB.prepare("DELETE FROM settings WHERE key IN ('default_model','worker_model')").run();
});

const put = (body: unknown) => api("/v1/settings", { method: "PUT", body: JSON.stringify(body) });

describe("model settings on the cloud core", () => {
  it("saves the default and worker model, shows them back, and clears them with null", async () => {
    install([textTurn("ok")]);
    expect(((await (await put({ default_model: "vendor/big", worker_model: "vendor/small" })).json()) as Record<string, unknown>)).toMatchObject({ default_model: "vendor/big", worker_model: "vendor/small" });
    expect(await (await api("/v1/settings")).json()).toMatchObject({ default_model: "vendor/big" });
    await put({ default_model: null, worker_model: null });
    expect(await (await api("/v1/settings")).json()).toMatchObject({ default_model: "z-ai/glm-5.3", worker_model: "z-ai/glm-5.3-flash" });
  });

  it("uses the saved default model for a task that names none", async () => {
    const fx = install([textTurn("ok")]);
    await put({ default_model: "vendor/big" });
    const { session_id } = await startTask({ goal: "hello" });
    await readSse(await api(`/v1/sessions/${session_id}/events`));
    expect(fx.script.requests[0]?.model).toBe("vendor/big");
  });

  it("rejects a bad model name, an empty body, and explains why a custom endpoint is not available here", async () => {
    install([textTurn("ok")]);
    expect((await put({ default_model: "has spaces" })).status).toBe(400);
    expect((await put({})).status).toBe(400);
    const custom = await put({ custom_endpoint_url: "https://llm.example.com/v1" });
    expect(custom.status).toBe(400);
    expect(JSON.stringify(await custom.json())).toContain("self-hosted or local core");
  });

  it("holds the default model to the policy's allowed models", async () => {
    const fx = install([textTurn("ok")]);
    fx.setPolicy('{"allowed_models":["z-ai/glm-5.3"]}');
    expect((await put({ default_model: "other/model" })).status).toBe(403);
    expect((await put({ default_model: "z-ai/glm-5.3" })).status).toBe(200);
  });
});
