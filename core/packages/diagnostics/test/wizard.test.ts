import { describe, expect, it } from "vitest";
import { buildReport, runWizard, stepsFor, type Ctx, type ControlPlane, type Row } from "../src/wizard.ts";
import { parseJsonc, renderDeployConfig } from "../src/jsonc.ts";
import { formatRow } from "../src/cli.ts";

const TOKEN = "tok-123";

type Handler = (path: string, init: RequestInit) => Response | Promise<Response>;

function ctx(handler: Handler, extra: Partial<Ctx> = {}): Ctx {
  let t = 0;
  return {
    baseUrl: "https://core.test",
    token: TOKEN,
    appVersion: "0.1.0",
    backend: "cloudflare",
    fetch: (async (url: string, init: RequestInit) => {
      t += 5;
      const u = new URL(url);
      return handler(u.pathname + u.search, init);
    }) as unknown as typeof fetch,
    now: () => (t += 1),
    pings: 5,
    cache: {},
    ...extra,
  };
}

const j = (body: unknown, status = 200): Response => new Response(JSON.stringify(body), { status });
const sseStream = (n: number): Response => new Response(Array.from({ length: n }, (_, i) => `id: ${i}\ndata: {}\n\n`).join(""));

function healthy(over: Record<string, () => Response> = {}): Handler {
  const routes: Record<string, () => Response> = {
    "/v1/health": () => j({ ok: true, version: "0.1.3" }),
    "/v1/diag/bindings": () => j({ ok: true, version: "0.1.3", d1: true, r2: true, model_key: true, durable_objects: true, workflow: true, container: true }),
    "/v1/diag/openrouter/auth": () => j({ ok: true, ms: 120, limit: 10, usage: 1, limit_remaining: 9 }),
    "/v1/diag/openrouter/models": () => j({ ok: true, models: [{ id: "a/m", available: true, tools: true }] }),
    "/v1/diag/openrouter/completion": () => j({ ok: true, model: "a/m", tool_calls: 1, reasoning_tokens: 4, reasoning_sample: "hmm", cost_usd: 0.002, ttft_ms: 800, tokens_per_sec: 55, total_ms: 3000, via_gateway: false }),
    "/v1/diag/perf": () => j({ ok: true, d1_write_ms: { p50: 5, p95: 9 }, d1_read_ms: { p50: 2, p95: 4 }, r2_put_ms: { p50: 40, p95: 60 }, r2_get_ms: { p50: 30, p95: 50 }, r2_put_mib_per_s: 25, r2_get_mib_per_s: 33, edge_to_openrouter_ms: { p50: 90, p95: 120 }, gateway_overhead_ms: null }),
    "/v1/diag/sandbox": () => j({ ok: true, cold_ms: 2400, warm_ms: 30, key_visible: false, key_shaped_variables: 0, egress_blocked: true }),
    "/v1/diag/sandbox/stops": () => j({ ok: true, total: 3, clean: 2, deliberate: 1, crashed: 0, stops: [] }),
    "/v1/diag/memory": () => j({ ok: true, citation: "task:diag", ms: 12 }),
    "/v1/diag/audit": () => j({ ok: true, entries: 4, day_spent_usd: 0.002, daily_cap_usd: 5, diag_spend_rows: 1, diag_spend_usd: 0.002 }),
    "/v1/diag/stream?n=10": () => sseStream(10),
    ...over,
  };
  return (path) => (routes[path] ? (routes[path] as () => Response)() : j({}, 404));
}

const byId = (rows: Row[], id: string): Row => rows.find((r) => r.id === id) as Row;

describe("wizard chain order", () => {
  it("lists the nine groups in the real order", () => {
    const groups = [...new Set(stepsFor("cloudflare").map((d) => d.group))];
    expect(groups).toEqual([
      "1. Cloudflare account",
      "2. Provision and deploy",
      "3. Store keys",
      "4. Backend health",
      "5. OpenRouter",
      "6. Connection speed and latency",
      "7. Sandbox",
      "8. Memory",
      "9. Audit and spend",
    ]);
  });

  it("puts OpenRouter auth, then models, then the streamed call, in that order", () => {
    const ids = stepsFor("cloudflare").map((d) => d.id);
    expect(ids.indexOf("or.auth")).toBeLessThan(ids.indexOf("or.models"));
    expect(ids.indexOf("or.models")).toBeLessThan(ids.indexOf("or.stream"));
    expect(ids.indexOf("or.stream")).toBeLessThan(ids.indexOf("net.rtt"));
  });
});

describe("web wizard against a healthy core", () => {
  it("passes every row, shows timing and cost, and marks running rows before they finish", async () => {
    const seen: string[] = [];
    const rows = await runWizard(stepsFor("cloudflare"), ctx(healthy()), (r) => {
      const running = r.find((x) => x.status === "running");
      if (running && seen[seen.length - 1] !== running.id) seen.push(running.id);
    });
    expect(rows.every((r) => r.status === "pass")).toBe(true);
    expect(seen).toEqual(stepsFor("cloudflare").map((d) => d.id));
    expect(rows.every((r) => typeof r.ms === "number")).toBe(true);
    const report = buildReport(rows, { backend: "cloudflare", appVersion: "0.1.0" });
    expect(report.test_cost_usd).toBeCloseTo(0.002);
    expect(report.failed).toBe(0);
    expect(byId(rows, "net.rtt").metrics?.map((m) => m.label)).toEqual(["p50", "p95", "first (cold connection)", "pings"]);
    expect(byId(rows, "or.stream").metrics?.some((m) => m.label.startsWith("cost"))).toBe(true);
  });

  it("sends the token only as a bearer header and never puts it in a URL or result", async () => {
    const urls: string[] = [];
    const rows = await runWizard(stepsFor("cloudflare"), ctx((p, init) => {
      urls.push(p);
      expect(new Headers(init.headers).get("authorization")).toBe(`Bearer ${TOKEN}`);
      return healthy()(p, init);
    }), () => {});
    expect(urls.some((u) => u.includes(TOKEN))).toBe(false);
    expect(JSON.stringify(rows)).not.toContain(TOKEN);
  });
});

describe("failures are plain and skip what depends on them", () => {
  it("a wrong token fails at health with a fix, and later rows are skipped", async () => {
    const rows = await runWizard(stepsFor("cloudflare"), ctx(() => j({}, 401)), () => {});
    expect(byId(rows, "cf.account").status).toBe("fail");
    expect(byId(rows, "cf.account").fix?.action).toBe("enter-token");
    expect(byId(rows, "or.auth").status).toBe("skip");
    expect(byId(rows, "or.auth").detail).toContain("did not pass");
  });

  it("an unreachable backend reports it and offers a retry", async () => {
    const rows = await runWizard(stepsFor("cloudflare"), ctx(() => { throw new TypeError("boom"); }), () => {});
    const r = byId(rows, "cf.account");
    expect(r.status).toBe("fail");
    expect(r.detail).toContain("could not be reached");
    expect(r.fix?.action).toBe("retry");
  });

  it("an out-of-credit key fails the OpenRouter group with a credit fix and skips the model rows", async () => {
    const rows = await runWizard(stepsFor("cloudflare"), ctx(healthy({ "/v1/diag/openrouter/auth": () => j({ ok: false, code: "payment_required" }) })), () => {});
    expect(byId(rows, "or.auth")).toMatchObject({ status: "fail", fix: { action: "open:openrouter-credits" } });
    expect(byId(rows, "or.models").status).toBe("skip");
    expect(byId(rows, "or.stream").status).toBe("skip");
    // Speed rows that do not need the model still run.
    expect(byId(rows, "net.rtt").status).toBe("pass");
  });

  it("a refused gateway is reported as the gateway refusing, never as OpenRouter rejecting the key", async () => {
    const rows = await runWizard(stepsFor("cloudflare"), ctx(healthy({ "/v1/diag/openrouter/auth": () => j({ ok: false, code: "gateway_refused" }) })), () => {});
    const r = byId(rows, "or.auth");
    expect(r.status).toBe("fail");
    expect(r.detail).toBe("AI Gateway refused the request (needs authentication).");
    expect(r.detail).not.toMatch(/rejected the key/);
    expect(r.fix?.text).not.toMatch(/Create a new key/);
  });

  it("a version mismatch fails the health row and names both versions", async () => {
    const rows = await runWizard(stepsFor("cloudflare"), ctx(healthy({ "/v1/health": () => j({ ok: true, version: "0.2.0" }) })), () => {});
    expect(byId(rows, "backend.health").status).toBe("fail");
    expect(byId(rows, "backend.health").detail).toContain("0.2.0");
    expect(byId(rows, "backend.health").fix?.action).toBe("update-core");
  });

  it("a visible key or open egress is a failure, not a warning", async () => {
    const rows = await runWizard(stepsFor("cloudflare"), ctx(healthy({ "/v1/diag/sandbox": () => j({ ok: false, code: "key_visible", cold_ms: 100, warm_ms: 10, key_visible: true, key_shaped_variables: 1, egress_blocked: true }) })), () => {});
    expect(byId(rows, "sandbox.keys")).toMatchObject({ status: "fail" });
    expect(byId(rows, "sandbox.keys").detail).toContain("secret is visible");
  });

  it("a missing Container binding on the web wizard explains the plan requirement", async () => {
    const rows = await runWizard(stepsFor("cloudflare"), ctx(healthy({ "/v1/diag/bindings": () => j({ ok: true, d1: true, r2: true, container: false, durable_objects: true, workflow: true, model_key: true }) })), () => {});
    expect(byId(rows, "cf.features")).toMatchObject({ status: "fail", fix: { action: "open:cloudflare-billing" } });
    expect(byId(rows, "cf.features").fix?.text).toContain("Workers Paid");
  });

  it("a buffered event stream that delivers too few events fails", async () => {
    const rows = await runWizard(stepsFor("cloudflare"), ctx(healthy({ "/v1/diag/stream?n=10": () => sseStream(4) })), () => {});
    expect(byId(rows, "net.sse").status).toBe("fail");
  });
});

describe("control plane rows (CLI)", () => {
  it("use the control plane when present, in order, and stop on its first failure", async () => {
    const calls: string[] = [];
    const control: ControlPlane = {
      async run(step) {
        calls.push(step);
        return step === "features" ? { ok: false, detail: "Some required features are not enabled.", fix: { text: "Upgrade.", action: "open:cloudflare-billing" } } : { ok: true, detail: "ok" };
      },
    };
    const rows = await runWizard(stepsFor("cloudflare"), ctx(healthy(), { control }), () => {});
    expect(calls).toEqual(["account", "features"]);
    expect(byId(rows, "deploy.provision").status).toBe("skip");
    expect(byId(rows, "backend.health").status).toBe("skip");
  });
});

describe("self-hosted and local backends", () => {
  it("run the same nine groups, with the Cloudflare rows shown as not applicable", async () => {
    for (const b of ["self-hosted", "local"] as const) {
      expect(stepsFor(b).map((d) => d.id)).toEqual(stepsFor("cloudflare").map((d) => d.id));
      const rows = await runWizard(stepsFor(b), ctx(healthy(), { backend: b }), () => {});
      const na = rows.filter((r) => r.notApplicable).map((r) => r.id);
      expect(na).toEqual(["cf.account", "cf.features", "deploy.provision", "deploy.worker", "keys.model"]);
      expect(rows.filter((r) => !r.notApplicable).every((r) => r.status === "pass")).toBe(true);
      const report = buildReport(rows, { backend: b, appVersion: "0.1.0" });
      expect(report).toMatchObject({ failed: 0, skipped: 0, not_applicable: 5 });
    }
  });

  it("a not-applicable row never blocks the rows after it, and a real failure still does", async () => {
    const rows = await runWizard(stepsFor("local"), ctx(healthy({ "/v1/diag/openrouter/auth": () => j({ ok: false, code: "key_missing" }) }), { backend: "local" }), () => {});
    expect(byId(rows, "backend.health").status).toBe("pass");
    expect(byId(rows, "or.auth").status).toBe("fail");
    expect(byId(rows, "or.models").status).toBe("skip");
    expect(byId(rows, "or.models").notApplicable).toBeUndefined();
  });
});

describe("helpers", () => {
  it("parses wrangler-style JSONC and keeps comment markers inside strings", () => {
    const v = parseJsonc('{\n // c\n "a": "http://x//y", /* b */ "b": [1, 2,],\n}');
    expect(v).toEqual({ a: "http://x//y", b: [1, 2] });
  });

  it("fills the deploy config without touching the committed placeholders", () => {
    const base = parseJsonc('{"name":"n","workers_dev":false,"d1_databases":[{"binding":"DB","database_id":"0"}],"secrets_store_secrets":[{"binding":"A","store_id":"p"}],"vars":{"X":"1"}}');
    const out = renderDeployConfig(base, { databaseId: "db", storeId: "st", workerName: "w" });
    expect(out).toMatchObject({ name: "w", workers_dev: true, d1_databases: [{ database_id: "db" }], secrets_store_secrets: [{ store_id: "st" }], vars: { X: "1", AI_GATEWAY_ID: "" } });
    expect(base.d1_databases[0].database_id).toBe("0");
  });

  it("adds the Workers AI binding and the gateway id, and never an account id, when a gateway name is given", () => {
    const base = parseJsonc('{"name":"n","vars":{}}');
    const out = renderDeployConfig(base, { databaseId: "db", storeId: "st", gatewayId: "open-computer" });
    expect(out.ai).toEqual({ binding: "AI" });
    expect(out.vars).toMatchObject({ AI_GATEWAY_ID: "open-computer", AI_GATEWAY_ACCOUNT: "" });
    expect(renderDeployConfig(base, { databaseId: "db", storeId: "st" }).ai).toBeUndefined();
    expect(base.ai).toBeUndefined();
  });

  it("formats rows for the terminal with timing and the fix", () => {
    const lines = formatRow({ id: "x", group: "g", title: "T", status: "fail", ms: 1500, detail: "bad", fix: { text: "do it" }, metrics: [{ label: "a", value: "1" }] });
    expect(lines.join("\n")).toContain("[FAIL] T  (1.5 s)");
    expect(lines.join("\n")).toContain("Fix: do it");
  });
});

describe("sandbox stops row", () => {
  it("passes while no recorded sandbox stop crashed, and lists the counts", async () => {
    const rows = await runWizard(stepsFor("cloudflare"), ctx(healthy()), () => {});
    expect(byId(rows, "sandbox.stops")).toMatchObject({ status: "pass" });
    expect(byId(rows, "sandbox.stops").detail).toContain("last 3 sandbox stops");
    expect(byId(rows, "sandbox.stops").metrics?.some((m) => m.label === "crashed" && m.value === "0")).toBe(true);
  });

  it("fails with the core's words when a sandbox crashed", async () => {
    const rows = await runWizard(
      stepsFor("cloudflare"),
      ctx(healthy({ "/v1/diag/sandbox/stops": () => j({ ok: false, total: 4, clean: 3, deliberate: 0, crashed: 1, message: "1 of the last 4 sandbox stops were crashes (exited exit 137)." }) })),
      () => {},
    );
    expect(byId(rows, "sandbox.stops")).toMatchObject({ status: "fail" });
    expect(byId(rows, "sandbox.stops").detail).toContain("exit 137");
    expect(byId(rows, "sandbox.stops").fix?.text).toContain("Containers");
  });

  it("an unclean stop of the test sandbox fails only the stops row, not the run or the key row", async () => {
    const rows = await runWizard(
      stepsFor("cloudflare"),
      ctx(healthy({ "/v1/diag/sandbox": () => j({ ok: false, code: "sandbox_unclean_stop", stop: "crashed", cold_ms: 100, warm_ms: 10, key_visible: false, egress_blocked: true, message: "The sandbox did not stop cleanly (exited, exit code 137)." }) })),
      () => {},
    );
    expect(byId(rows, "sandbox.run")).toMatchObject({ status: "pass" });
    expect(byId(rows, "sandbox.keys")).toMatchObject({ status: "pass" });
    expect(byId(rows, "sandbox.stops")).toMatchObject({ status: "fail" });
    expect(byId(rows, "sandbox.stops").detail).toContain("did not stop cleanly");
  });
});

