// Control plane for the CLI: talks to the owner's Cloudflare account through Wrangler (their own login, or a scoped
// CLOUDFLARE_API_TOKEN). Secrets only ever travel on a child process's stdin, never in argv, files or output.
import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { chmodSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";
import { parseJsonc, renderDeployConfig } from "./jsonc.ts";
import { ms1, type ControlPlane, type Outcome } from "./wizard.ts";

export interface RunResult {
  code: number;
  out: string;
}

export type Runner = (args: string[], opts?: { input?: string; cwd?: string }) => Promise<RunResult>;

const HEX32 = /[0-9a-f]{32}/;

/** Strips ids and anything key-shaped from tool output before it is shown. */
export function scrub(text: string): string {
  return text.replace(/[0-9a-f]{32}/g, "<id>").replace(/\b(sk|cf)[-_][A-Za-z0-9_-]{16,}\b/g, "<redacted>");
}

export function wranglerRunner(cwd: string, wrangler: string[] = (process.env.OC_WRANGLER ?? "pnpm exec wrangler").split(" ")): Runner {
  return (args, opts = {}) =>
    new Promise((resolve) => {
      const [cmd, ...rest] = wrangler;
      const child = spawn(cmd as string, [...rest, ...args], { cwd: opts.cwd ?? cwd, stdio: ["pipe", "pipe", "pipe"], env: { ...process.env, NO_COLOR: "1", WRANGLER_SEND_METRICS: "false" } });
      let out = "";
      child.stdout.on("data", (d: Buffer) => (out += d.toString()));
      child.stderr.on("data", (d: Buffer) => (out += d.toString()));
      child.on("error", () => resolve({ code: 127, out: "wrangler could not be started" }));
      child.on("close", (code) => resolve({ code: code ?? 1, out }));
      if (opts.input !== undefined) child.stdin.end(opts.input);
      else child.stdin.end();
    });
}

export interface ControlOptions {
  run: Runner;
  /** The cloud package directory (holds wrangler.jsonc). */
  cloudDir: string;
  workerName?: string;
  databaseName?: string;
  bucketName?: string;
  /** Where the access token and core URL are kept for the CLI and desktop (0600). */
  stateDir?: string;
  /** Returns the OpenRouter key from the environment without it ever being printed. */
  modelKey?: () => string | undefined;
  /** Optional extra key slots by name (for example "search" or "coding"), stored as `oc_key_<name>`. Values never printed. */
  extraKeys?: () => Record<string, string>;
  log?: (line: string) => void;
}

export function defaultStateDir(): string {
  return path.join(process.env.XDG_CONFIG_HOME ?? path.join(homedir(), ".config"), "open-computer");
}

export interface CoreState {
  url?: string;
  token?: string;
}

export function readState(dir = defaultStateDir()): CoreState {
  try {
    return JSON.parse(readFileSync(path.join(dir, "cloud.json"), "utf8")) as CoreState;
  } catch {
    return {};
  }
}

export function writeState(patch: CoreState, dir = defaultStateDir()): void {
  mkdirSync(dir, { recursive: true, mode: 0o700 });
  const file = path.join(dir, "cloud.json");
  writeFileSync(file, JSON.stringify({ ...readState(dir), ...patch }, null, 2), { mode: 0o600 });
  chmodSync(file, 0o600);
}

const SECRET_NAMES = { access: "oc_access_secret", model: "oc_model_key" } as const;

export function createControlPlane(o: ControlOptions): ControlPlane {
  const run = o.run;
  const worker = o.workerName ?? "open-computer-core";
  const dbName = o.databaseName ?? "open-computer";
  const bucket = o.bucketName ?? "open-computer-artifacts";
  const stateDir = o.stateDir ?? defaultStateDir();
  const deployCfg = path.join(o.cloudDir, "wrangler.deploy.jsonc");
  const mem: { dbId?: string; storeId?: string } = {};

  const fail = (detail: string, fix: string, action = "retry"): Outcome => ({ ok: false, detail, fix: { text: fix, action } });

  async function storeId(): Promise<string | null> {
    if (mem.storeId) return mem.storeId;
    const r = await run(["secrets-store", "store", "list", "--remote"]);
    if (r.code !== 0) return null;
    const id = HEX32.exec(r.out)?.[0];
    if (id) {
      mem.storeId = id;
      return id;
    }
    const c = await run(["secrets-store", "store", "create", "default_secrets_store", "--remote"]);
    const made = HEX32.exec(c.out)?.[0];
    if (made) mem.storeId = made;
    return made ?? null;
  }

  async function hasSecret(store: string, name: string): Promise<boolean | null> {
    const r = await run(["secrets-store", "secret", "list", store, "--remote"]);
    if (r.code !== 0) return null;
    return r.out.split("\n").some((l) => l.includes(`│ ${name} `));
  }

  async function putSecret(store: string, name: string, value: string): Promise<boolean> {
    const r = await run(["secrets-store", "secret", "create", store, "--name", name, "--scopes", "workers", "--remote"], { input: value });
    return r.code === 0;
  }

  async function databaseId(): Promise<string | null> {
    if (mem.dbId) return mem.dbId;
    const list = await run(["d1", "list", "--json"]);
    if (list.code === 0) {
      try {
        const found = (JSON.parse(list.out.slice(list.out.indexOf("["))) as Array<{ uuid: string; name: string }>).find((d) => d.name === dbName);
        if (found) return (mem.dbId = found.uuid);
      } catch {
        /* fall through to create */
      }
    }
    const created = await run(["d1", "create", dbName]);
    const id = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/.exec(created.out)?.[0];
    if (id) mem.dbId = id;
    return id ?? null;
  }

  return {
    async run(step) {
      const t0 = Date.now();
      switch (step) {
        case "account": {
          const r = await run(["whoami"]);
          if (r.code !== 0 || /not authenticated|not logged in/i.test(r.out)) {
            return fail("You are not signed in to Cloudflare.", "Run `wrangler login`, or set CLOUDFLARE_API_TOKEN to a token with Workers, D1, R2, Secrets Store and Containers write access.", "login");
          }
          const needs = ["workers", "d1", "secrets_store", "containers"];
          const missing = needs.filter((n) => !new RegExp(`${n}\\s*\\((write|edit)`, "i").test(r.out));
          const viaToken = !!process.env.CLOUDFLARE_API_TOKEN;
          if (missing.length > 0 && !viaToken) {
            return fail(`The login is missing these permissions: ${missing.join(", ")}.`, "Run `wrangler login` again and accept every permission, or use a scoped API token.", "login");
          }
          return { ok: true, detail: `Signed in${viaToken ? " with an API token" : ""}; the account is reachable.`, metrics: [{ label: "time", value: ms1(Date.now() - t0) }] };
        }
        case "features": {
          const probes: Array<[string, string[], string]> = [
            ["D1", ["d1", "list", "--json"], "Enable D1 in the Cloudflare dashboard."],
            ["R2", ["r2", "bucket", "list"], "Enable R2 (it needs a payment method on file, with a free allowance) in the dashboard."],
            ["Secrets Store", ["secrets-store", "store", "list", "--remote"], "Secrets Store is in open beta; enable it in the dashboard under Secrets Store."],
            ["Containers", ["containers", "list"], "Containers and Durable Objects need the Workers Paid plan ($5 a month). Upgrade under Workers & Pages > Plans."],
          ];
          const m: Array<{ label: string; value: string }> = [];
          const bad: string[] = [];
          for (const [name, args, hint] of probes) {
            const r = await run(args);
            m.push({ label: name, value: r.code === 0 ? "available" : "not available" });
            if (r.code !== 0) bad.push(hint);
          }
          return bad.length === 0 ? { ok: true, detail: "Everything the core needs is enabled on this account.", metrics: m } : { ok: false, detail: "Some required features are not enabled.", metrics: m, fix: { text: bad.join(" "), action: "open:cloudflare-billing" } };
        }
        case "provision": {
          const sid = await storeId();
          if (!sid) return fail("Secrets Store could not be reached or created.", "Enable Secrets Store in the Cloudflare dashboard, then retry.");
          const db = await databaseId();
          if (!db) return fail("The D1 database could not be created.", "Check that D1 is enabled on the account, then retry.");
          const rl = await run(["r2", "bucket", "list"]);
          if (rl.code !== 0) return fail("R2 could not be reached.", "Enable R2 in the dashboard, then retry.");
          const exists = rl.out.split("\n").some((l) => l.trim() === `name:           ${bucket}`);
          if (!exists) {
            const c = await run(["r2", "bucket", "create", bucket]);
            if (c.code !== 0 && !/already exists/i.test(c.out)) return fail("The R2 bucket could not be created.", "Check R2 is enabled, then retry.");
          }
          const have = await hasSecret(sid, SECRET_NAMES.access);
          if (have === null) return fail("Secrets Store did not answer.", "Retry in a minute.");
          let created = false;
          if (!have) {
            const token = randomBytes(32).toString("hex");
            if (!(await putSecret(sid, SECRET_NAMES.access, token))) return fail("The access secret could not be stored.", "Retry.");
            writeState({ token }, stateDir);
            created = true;
          }
          const cfg = renderDeployConfig(parseJsonc(readFileSync(path.join(o.cloudDir, "wrangler.jsonc"), "utf8")), { databaseId: db, storeId: sid, workerName: worker });
          writeFileSync(deployCfg, JSON.stringify(cfg, null, 2), { mode: 0o600 });
          const mig = await run(["d1", "migrations", "apply", dbName, "--remote", "-c", deployCfg]);
          if (mig.code !== 0) return fail("The database migration failed.", `Retry. Detail: ${scrub(mig.out).split("\n").slice(-3).join(" ").slice(0, 200)}`);
          return {
            ok: true,
            detail: created ? "Created the database, bucket and access secret, and applied the schema." : "Everything already existed; schema is current.",
            metrics: [{ label: "time", value: ms1(Date.now() - t0) }],
          };
        }
        case "deploy": {
          if (!existsSync(deployCfg)) return fail("Nothing to deploy yet.", "Run the provision step first.");
          const sid = await storeId();
          // The Worker binds both secrets by name; a missing model key is created empty-safe only when a key is supplied.
          if (sid && (await hasSecret(sid, SECRET_NAMES.model)) === false) {
            const key = o.modelKey?.();
            if (key && !(await putSecret(sid, SECRET_NAMES.model, key))) return fail("The OpenRouter key could not be stored.", "Retry.");
          }
          const r = await run(["deploy", "-c", deployCfg]);
          if (r.code !== 0) {
            const text = scrub(r.out);
            if (/container|docker/i.test(text) && /fail|error|denied|cannot/i.test(text)) {
              return fail("The sandbox image could not be built or pushed.", "Make sure Docker is running (Containers need a local image build) and the account is on Workers Paid, then retry.", "retry");
            }
            return fail("The deploy failed.", `Retry. Detail: ${text.split("\n").slice(-4).join(" ").slice(0, 240)}`);
          }
          const url = /https:\/\/[a-z0-9.-]+\.workers\.dev/i.exec(r.out)?.[0];
          if (url) writeState({ url }, stateDir);
          return { ok: true, detail: url ? "Deployed. The core is live on your workers.dev address (protected by its access token)." : "Deployed.", metrics: [{ label: "time", value: ms1(Date.now() - t0) }] };
        }
        case "keys": {
          const sid = await storeId();
          if (!sid) return fail("Secrets Store could not be reached.", "Retry.");
          const have = await hasSecret(sid, SECRET_NAMES.model);
          let detail = "An OpenRouter key is already stored (its value is never shown).";
          if (!have) {
            const key = o.modelKey?.();
            if (!key) return fail("No OpenRouter key was provided.", "Set OC_OPENROUTER_KEY in this shell (do not paste it into the command line) and run setup again.", "store-key");
            if (!(await putSecret(sid, SECRET_NAMES.model, key))) return fail("The key could not be stored.", "Retry.");
            detail = "The OpenRouter key is stored in Secrets Store (not shown, not logged).";
          }
          const stored: string[] = [];
          for (const [name, value] of Object.entries(o.extraKeys?.() ?? {})) {
            if (!/^[a-z][a-z0-9_]{0,31}$/.test(name) || !value) return fail(`The extra key slot "${name}" has a bad name or no value.`, "Slot names are lowercase letters, digits and underscores.", "store-key");
            if ((await hasSecret(sid, `oc_key_${name}`)) === true) continue;
            if (!(await putSecret(sid, `oc_key_${name}`, value))) return fail(`The extra key "${name}" could not be stored.`, "Retry.");
            stored.push(name);
          }
          return { ok: true, detail: stored.length ? `${detail} Extra slots stored: ${stored.join(", ")}.` : detail };
        }
      }
    },
  };
}
