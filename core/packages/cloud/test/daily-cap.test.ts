import { beforeEach, describe, expect, it } from "vitest";
import defaults from "../../../contracts/defaults.json" with { type: "json" };
import { api, install, readSse, seedSecrets, startTask, textTurn, type Fixture } from "./support.ts";
import { env } from "./env.ts";

let fx: Fixture;

beforeEach(async () => {
  await seedSecrets();
  await env.DB.prepare("DELETE FROM settings").run();
  fx = install([textTurn("ok")]);
});

const put = (body: unknown) => api("/v1/settings", { method: "PUT", body: JSON.stringify(body) });
const getSettings = async () => (await (await api("/v1/settings")).json()) as Record<string, unknown>;
// Read each task to its end, so none is still running (and calling the provider) when the next test starts.
const capOf = async (body: Record<string, unknown> = {}) => {
  const started = await startTask({ goal: "cap check", ...body });
  await readSse(await api(`/v1/sessions/${started.session_id}/events`));
  return started.day_cap;
};

describe("the default daily cap", () => {
  it("is five dollars from the shared defaults file, and a user who set nothing gets it", async () => {
    expect(defaults.daily_cap_usd).toBe(5);
    expect(await getSettings()).toEqual({
      default_model: "z-ai/glm-5.3",
      worker_model: "z-ai/glm-5.3-flash",
      daily_cap_usd: null,
      default_daily_cap_usd: 5,
      max_daily_cap_usd: 1000,
      policy_max_daily_cap_usd: null,
      effective_daily_cap_usd: 5,
    });
    expect(await capOf()).toBe(5);
  });

  it("is applied to a task that omits a cap or sends null, and shows in the cost event", async () => {
    expect(await capOf({ day_cap: null })).toBe(5);
    const { session_id } = await startTask({ goal: "cost event" });
    const { events } = await readSse(await api(`/v1/sessions/${session_id}/events`));
    expect(events.find((e) => e.type === "cost")).toMatchObject({ day_cap: 5 });
  });
});

describe("the user's own daily cap", () => {
  it("can be raised above the default, persists, and becomes what a task gets", async () => {
    const res = await put({ daily_cap_usd: 20 });
    expect(res.status).toBe(200);
    expect(await res.json()).toMatchObject({ daily_cap_usd: 20, effective_daily_cap_usd: 20, default_daily_cap_usd: 5 });
    expect(await getSettings()).toMatchObject({ daily_cap_usd: 20, effective_daily_cap_usd: 20 });
    expect(await capOf()).toBe(20);
  });

  it("can be lowered below the default", async () => {
    await put({ daily_cap_usd: 2 });
    expect(await capOf()).toBe(2);
  });

  it("goes back to the default when it is set to null", async () => {
    await put({ daily_cap_usd: 20 });
    const res = await put({ daily_cap_usd: null });
    expect(await res.json()).toMatchObject({ daily_cap_usd: null, effective_daily_cap_usd: 5 });
    expect(await capOf()).toBe(5);
  });

  it("lets a task ask for a lower cap than the user's, never a higher one", async () => {
    await put({ daily_cap_usd: 20 });
    expect(await capOf({ day_cap: 3 })).toBe(3);
    expect(await capOf({ day_cap: 500 })).toBe(20);
  });

  it("holds a task to the default when the user never raised it", async () => {
    expect(await capOf({ day_cap: 50 })).toBe(5);
  });

  it("is enforced while a task runs: a cap too small for the next call stops it", async () => {
    await put({ daily_cap_usd: 0.2 });
    const { session_id } = await startTask({ goal: "too small a day" });
    const { events } = await readSse(await api(`/v1/sessions/${session_id}/events`));
    expect(events.at(-1)).toMatchObject({ type: "run", status: "failed", reason: expect.stringContaining("daily spend cap") });
    expect(fx.script.calls).toBe(0);
  });
});

describe("validation of the user's cap", () => {
  it("accepts any positive number up to the bound, including the bound itself", async () => {
    for (const v of [0.01, 1, 5, 999.99, 1000]) expect((await put({ daily_cap_usd: v })).status, String(v)).toBe(200);
    expect(await getSettings()).toMatchObject({ daily_cap_usd: 1000 });
  });

  it("rejects zero, negatives, values over the bound, text, booleans and missing or unknown fields, and keeps the old value", async () => {
    await put({ daily_cap_usd: 7 });
    for (const bad of [{ daily_cap_usd: 0 }, { daily_cap_usd: -3 }, { daily_cap_usd: 1000.01 }, { daily_cap_usd: 5000 }, { daily_cap_usd: "5" }, { daily_cap_usd: true }, {}, { daily_cap_usd: 5, colour: "red" }]) {
      const res = await put(bad);
      expect(res.status, JSON.stringify(bad)).toBe(400);
    }
    expect(await getSettings()).toMatchObject({ daily_cap_usd: 7 });
  });

  it("rejects a body that is not JSON", async () => {
    const res = await api("/v1/settings", { method: "PUT", body: "not json", headers: { "content-type": "application/json" } });
    expect(res.status).toBe(400);
  });

  it("needs the access token like every other route", async () => {
    expect((await api("/v1/settings", { token: null })).status).toBe(401);
    expect((await api("/v1/settings", { method: "PUT", token: null, body: "{}" })).status).toBe(401);
  });
});

describe("an IT policy maximum", () => {
  const policy = (max: number) => JSON.stringify({ max_daily_cap_usd: max });

  it("overrides the user's setting when it is lower, and says so in the settings", async () => {
    await put({ daily_cap_usd: 20 });
    fx.setPolicy(policy(3));
    expect(await getSettings()).toMatchObject({ daily_cap_usd: 20, policy_max_daily_cap_usd: 3, effective_daily_cap_usd: 3 });
    expect(await capOf()).toBe(3);
    expect(await capOf({ day_cap: 2 })).toBe(2);
    expect(await capOf({ day_cap: 50 })).toBe(3);
  });

  it("only ever lowers: a user cap below the maximum stays as the user chose it", async () => {
    fx.setPolicy(policy(10));
    await put({ daily_cap_usd: 1 });
    expect(await capOf()).toBe(1);
  });

  it("holds the default too when the user set nothing", async () => {
    fx.setPolicy(policy(2));
    expect(await capOf()).toBe(2);
  });

  it("does not stop the user from saving a higher number, which takes effect only if the policy goes away", async () => {
    fx.setPolicy(policy(3));
    expect((await put({ daily_cap_usd: 50 })).status).toBe(200);
    expect(await capOf()).toBe(3);
    fx.setPolicy(undefined);
    expect(await capOf()).toBe(50);
  });

  it("treats empty text and an object without a maximum as no policy", async () => {
    for (const text of ["", "   ", "{}"]) {
      fx.setPolicy(text);
      expect(await capOf(), JSON.stringify(text)).toBe(5);
    }
  });

  it("fails closed on a policy that cannot be read: nothing runs and settings are not served", async () => {
    for (const bad of ["{not json", "[]", "42", '{"other_field": 1}', '{"max_daily_cap_usd": -1}', '{"max_daily_cap_usd": "3"}', '{"max_daily_cap_usd": 0}']) {
      fx.setPolicy(bad);
      const task = await api("/v1/tasks", { method: "POST", body: JSON.stringify({ goal: "g" }) });
      expect(task.status, bad).toBe(500);
      expect(((await task.json()) as { error: { code: string } }).error.code).toBe("policy_invalid");
      expect((await api("/v1/settings")).status, bad).toBe(500);
    }
  });
});
