#!/usr/bin/env node
// `oc doctor` runs the test chain against a deployed core. `oc setup` first provisions, deploys and stores keys.
import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createControlPlane, readState, withFallback, wranglerRunner } from "./control.ts";
import { updateCore } from "./update.ts";
import { ensureAccess, ensureGateway } from "./admin.ts";
import { NOT_AVAILABLE_BY_OAUTH, parseWhoami, signInArgs } from "./signin.ts";
import { spawnSync } from "node:child_process";
import { buildReport, runWizard, stepsFor, type Backend, type Ctx, type Row } from "./wizard.ts";

const here = path.dirname(fileURLToPath(import.meta.url));
const cloudDir = path.join(here, "..", "..", "cloud");

/** OC_EXTRA_KEY_<NAME>=value in the environment becomes the extra key slot <name>. */
export function extraKeysFromEnv(env: Record<string, string | undefined> = process.env): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(env)) {
    const m = /^OC_EXTRA_KEY_([A-Z][A-Z0-9_]*)$/.exec(k);
    if (m && v) out[(m[1] as string).toLowerCase()] = v;
  }
  return out;
}

function flag(args: string[], name: string): string | undefined {
  const i = args.indexOf(`--${name}`);
  return i >= 0 ? args[i + 1] : undefined;
}

const MARK: Record<string, string> = { pass: "[ok]  ", fail: "[FAIL]", skip: "[skip]", pending: "[ .. ]", running: "[ .. ]" };
const FRAMES = ["|", "/", "-", "\\"];

export function formatRow(r: Row, frame = 0): string[] {
  const mark = r.status === "running" ? `[ ${FRAMES[frame % 4]}  ]` : (MARK[r.status] ?? "");
  const lines = [`${mark} ${r.title}${r.ms !== undefined ? `  (${r.ms >= 1000 ? (r.ms / 1000).toFixed(1) + " s" : Math.round(r.ms) + " ms"})` : ""}`];
  if (r.detail && r.status !== "pending") lines.push(`         ${r.detail}`);
  for (const m of r.metrics ?? []) lines.push(`           ${m.label}: ${m.value}`);
  if (r.fix) lines.push(`         Fix: ${r.fix.text}`);
  return lines;
}

async function main(argv: string[]): Promise<number> {
  const [cmd, ...args] = argv;
  if (cmd === "login") {
    // Browser sign-in: Cloudflare's own consent page, click Allow. Wrangler keeps the session; nothing is pasted or printed.
    const wrangler = (process.env.OC_WRANGLER ?? "pnpm exec wrangler").split(" ");
    const login = spawnSync(wrangler[0] as string, [...wrangler.slice(1), ...signInArgs()], { cwd: cloudDir, stdio: "inherit" });
    const who = spawnSync(wrangler[0] as string, [...wrangler.slice(1), "whoami"], { cwd: cloudDir, encoding: "utf8" });
    const st = parseWhoami(`${who.stdout ?? ""}`);
    if (login.status !== 0 || !st.signedIn) {
      console.log("Sign-in did not finish. Run `oc login` again and click Allow in the browser.");
      return 1;
    }
    console.log(`Signed in${st.email ? ` as ${st.email}` : ""}${st.accounts.length ? ` (${st.accounts.join(", ")})` : ""}.`);
    console.log(`Access granted: ${st.scopes.join(", ")}`);
    console.log(`Not available through this sign-in: ${NOT_AVAILABLE_BY_OAUTH.join("; ")}. Turn those on with one click in the Cloudflare dashboard (see docs/runbooks/cloud-setup.md).`);
    return 0;
  }
  if (cmd === "admin") {
    // Needs a scoped token: OC_CF_ADMIN_TOKEN="$(omaseal get cloudflare oc-admin)" and OC_CF_ACCOUNT_ID.
    const token = process.env.OC_CF_ADMIN_TOKEN ?? "";
    const accountId = process.env.OC_CF_ACCOUNT_ID ?? "";
    if (!token || !accountId) {
      console.log("oc admin needs OC_CF_ADMIN_TOKEN (scopes: AI Gateway Edit and Read; Access Apps and Policies Edit) and OC_CF_ACCOUNT_ID in the environment.\nStore the token with: omaseal set cloudflare oc-admin");
      return 2;
    }
    const what = args[0];
    const out =
      what === "gateway"
        ? await ensureGateway((u, i) => fetch(u, i), { token, accountId })
        : what === "access"
          ? await ensureAccess((u, i) => fetch(u, i), { token, accountId, hostname: flag(args, "hostname") ?? "", emails: (flag(args, "emails") ?? "").split(",").filter(Boolean) })
          : { ok: false, message: "usage: oc admin <gateway | access --hostname H --emails a@b.c,d@e.f>" };
    console.log(out.message);
    return out.ok ? 0 : 1;
  }
  if (cmd === "update") {
    const state = readState();
    const url = flag(args, "url") ?? process.env.OC_CLOUD_URL ?? state.url ?? "";
    const token = process.env.OC_CLOUD_TOKEN ?? state.token ?? "";
    const result = await updateCore({
      run: wranglerRunner(cloudDir),
      config: path.join(cloudDir, "wrangler.deploy.jsonc"),
      workerName: "open-computer-core",
      healthy: async () => {
        try {
          const res = await fetch(`${url.replace(/\/+$/, "")}/v1/health`, { headers: { authorization: `Bearer ${token}` }, signal: AbortSignal.timeout(10_000) });
          return res.status === 200;
        } catch {
          return false;
        }
      },
    });
    console.log(result.message);
    return result.ok ? 0 : 1;
  }
  if (cmd !== "doctor" && cmd !== "setup") {
    console.log("usage: oc <login|doctor|setup|update|admin> [--backend cloudflare|self-hosted|local] [--url URL] [--json FILE] [--pings N] [--skip-model] [--rotate-token (setup: issue a new access token; the old one stops working)]\n  token: OC_CLOUD_TOKEN, or the saved state; the OpenRouter key for setup: OC_OPENROUTER_KEY (environment only)");
    return 2;
  }
  const backend = (flag(args, "backend") ?? "cloudflare") as Backend;
  const state = readState();
  const control = cmd === "setup" ? createControlPlane({ rotateToken: args.includes("--rotate-token"), probe: async (url, token) => (await fetch(`${url.replace(/\/+$/, "")}/v1/health`, { headers: { authorization: `Bearer ${token}` }, signal: AbortSignal.timeout(10_000) }).catch(() => null))?.status ?? 0, run: wranglerRunner(cloudDir), deployRun: withFallback(wranglerRunner(cloudDir, ["pnpm", "exec", "wrangler"]), wranglerRunner(cloudDir)), cloudDir, ...(process.env.OC_AI_GATEWAY_ID ? { gatewayId: process.env.OC_AI_GATEWAY_ID } : {}), modelKey: () => process.env.OC_OPENROUTER_KEY, extraKeys: extraKeysFromEnv }) : undefined;

  const ctx: Ctx = {
    baseUrl: flag(args, "url") ?? process.env.OC_CLOUD_URL ?? state.url ?? "",
    token: process.env.OC_CLOUD_TOKEN ?? state.token ?? "",
    appVersion: JSON.parse(readFileSync(path.join(here, "..", "package.json"), "utf8")).version as string,
    backend,
    fetch: (...a) => fetch(...a),
    now: () => performance.now(),
    control,
    pings: Number(flag(args, "pings") ?? 20),
    cache: {},
  };

  if (cmd === "setup" && !process.env.CLOUDFLARE_API_TOKEN) {
    // First step of setup is the sign-in itself: if nobody is signed in, open the browser page (click Allow).
    const wr = (process.env.OC_WRANGLER ?? "pnpm exec wrangler").split(" ");
    const who = spawnSync(wr[0] as string, [...wr.slice(1), "whoami"], { cwd: cloudDir, encoding: "utf8" });
    if (!parseWhoami(`${who.stdout ?? ""}`).signedIn) {
      console.log("Opening Cloudflare's sign-in page in your browser. Click Allow.");
      spawnSync(wr[0] as string, [...wr.slice(1), ...signInArgs()], { cwd: cloudDir, stdio: "inherit" });
    }
  }
  let defs = stepsFor(backend);
  // CI has no model key and no network to a model: --skip-model shows the OpenRouter rows as not applicable.
  if (args.includes("--skip-model")) {
    const MODEL_ROW = /^(or\.|net\.model$)/;
    defs = defs.map((d) =>
      MODEL_ROW.test(d.id) ? { ...d, requires: [], run: async () => ({ ok: true, notApplicable: true, detail: "Skipped on request (--skip-model): no model key in this environment." }) } : d,
    );
  }
  const tty = process.stdout.isTTY;
  let frame = 0;
  let printed = 0;
  const draw = (rows: Row[]): void => {
    const lines = rows.flatMap((r, i) => (i === 0 || rows[i - 1]?.group !== r.group ? [`\n${r.group}`] : []).concat(formatRow(r, frame)));
    if (tty) {
      if (printed > 0) process.stdout.write(`\x1b[${printed}A\x1b[0J`);
      process.stdout.write(lines.join("\n") + "\n");
      printed = lines.length;
    }
    last = rows;
  };
  let last: Row[] = [];
  const timer = tty ? setInterval(() => { frame += 1; if (last.length) draw(last); }, 120) : undefined;

  // The core address and token appear only after setup stores them, so re-read them before the runtime rows.
  const origRun = defs.find((d) => d.id === "backend.health")?.run;
  const health = defs.find((d) => d.id === "backend.health");
  if (health && origRun) {
    health.run = (c) => {
      const s = readState();
      if (!c.baseUrl) c.baseUrl = s.url ?? "";
      if (!c.token) c.token = s.token ?? "";
      return origRun(c);
    };
  }
  const rows = await runWizard(defs, ctx, draw);
  if (timer) clearInterval(timer);
  if (!tty) for (const line of rows.flatMap((r) => formatRow(r))) console.log(line);

  const report = buildReport(rows, { backend, appVersion: ctx.appVersion });
  console.log(`\n${report.passed} passed, ${report.failed} failed, ${report.skipped} skipped in ${(report.total_ms / 1000).toFixed(1)} s; test spend $${report.test_cost_usd.toFixed(5)}`);
  const out = flag(args, "json");
  if (out) writeFileSync(out, JSON.stringify(report, null, 2));
  return report.failed === 0 && report.skipped === 0 ? 0 : 1;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  main(process.argv.slice(2)).then((c) => process.exit(c));
}
