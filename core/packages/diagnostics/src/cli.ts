#!/usr/bin/env node
// `oc doctor` runs the test chain against a deployed core. `oc setup` first provisions, deploys and stores keys.
import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createControlPlane, readState, wranglerRunner } from "./control.ts";
import { buildReport, runWizard, stepsFor, type Backend, type Ctx, type Row } from "./wizard.ts";

const here = path.dirname(fileURLToPath(import.meta.url));
const cloudDir = path.join(here, "..", "..", "cloud");

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
  if (cmd !== "doctor" && cmd !== "setup") {
    console.log("usage: oc <doctor|setup> [--backend cloudflare|self-hosted|local] [--url URL] [--json FILE] [--pings N]\n  token: OC_CLOUD_TOKEN, or the saved state; the OpenRouter key for setup: OC_OPENROUTER_KEY (environment only)");
    return 2;
  }
  const backend = (flag(args, "backend") ?? "cloudflare") as Backend;
  const state = readState();
  const control = cmd === "setup" ? createControlPlane({ run: wranglerRunner(cloudDir), cloudDir, modelKey: () => process.env.OC_OPENROUTER_KEY }) : undefined;

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

  const defs = stepsFor(backend);
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
