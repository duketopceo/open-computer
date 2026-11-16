// Control plane for the CLI: talks to the owner's Cloudflare account through Wrangler (their own login, or a scoped
// CLOUDFLARE_API_TOKEN). Secrets only ever travel on a child process's stdin, never in argv, files or output.
import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { chmodSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";
import { parseJsonc, renderDeployConfig } from "./jsonc.ts";
import { parseWhoami } from "./signin.ts";
import { ms1, type ControlPlane, type Outcome } from "./wizard.ts";

export interface RunResult {
  code: number;
  out: string;
}

export type Runner = (args: string[], opts?: { input?: string; cwd?: string }) => Promise<RunResult>;

const HEX32 = /[0-9a-f]{32}/;

const ANSI = /\u001b\[[0-9;]*m/g;

/** Wrangler colours its output even with NO_COLOR set; the codes are removed before anything is matched or shown. */
export function stripAnsi(text: string): string {
  return text.replace(ANSI, "");
}

/** Wrangler's own words about what went wrong: its `[ERROR]` blocks and error lines, without the docker build progress (`#7 CACHED`),
 * the ruler and the telemetry notice. Scrubbed, at most `max` lines. When it printed none, the last lines of output. */
export function wranglerErrorLines(out: string, max = 6): string[] {
  const lines = stripAnsi(out)
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l !== "" && !/^#\d+ /.test(l) && !/^[─-]+$/.test(l) && !/^Cloudflare collects anonymous telemetry/.test(l) && !/wrangler \d+\.\d+/.test(l) && !/^🪵/.test(l));
  const start = lines.findIndex((l) => /\[ERROR\]|✘|^Error:/i.test(l));
  const picked = start >= 0 ? lines.slice(start) : lines.slice(-max);
  return picked.slice(0, max).map((l) => scrub(l));
}

/** Docker is the problem only when Wrangler says so. */
export function dockerProblem(out: string): boolean {
  return /Docker CLI|Docker daemon|docker: command not found|Cannot connect to the Docker|docker build (failed|exited)|failed to (build|push) (the )?(container )?image/i.test(stripAnsi(out));
}

/** Strips ids and anything key-shaped from tool output before it is shown. */
export function scrub(text: string): string {
  return text.replace(/[0-9a-f]{32}/g, "<id>").replace(/\b(sk|cf)[-_][A-Za-z0-9_-]{16,}\b/g, "<redacted>");
}

/** Tries `primary`; when it cannot be started at all (exit 127), runs `fallback` instead. */
export function withFallback(primary: Runner, fallback: Runner): Runner {
  return async (args, opts) => {
    const r = await primary(args, opts);
    return r.code === 127 ? fallback(args, opts) : r;
  };
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
  /** Issue a new access token even though one exists. Only `oc setup --rotate-token` sets it: every other run keeps the token. */
  rotateToken?: boolean;
  /** Asks the deployed core's health route with a token and returns the HTTP status (401: the token is not the core's). */
  probe?: (url: string, token: string) => Promise<number>;
  /** Runs `wrangler deploy`; defaults to `run`. The CLI gives it this repository's own Wrangler. */
  deployRun?: Runner;
  /** The cloud package directory (holds wrangler.jsonc). */
  cloudDir: string;
  workerName?: string;
  /** Name of an existing AI Gateway to route model calls through (the Workers AI binding finds its address). */
  gatewayId?: string;
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

  /** The id of a secret in the store listing (its row holds the name and then the 32-character id), or null. */
  async function secretId(store: string, name: string): Promise<string | null> {
    const r = await run(["secrets-store", "secret", "list", store, "--remote"]);
    if (r.code !== 0) return null;
    const row = r.out.split("\n").find((l) => l.includes(`│ ${name} `));
    return row ? (HEX32.exec(row)?.[0] ?? null) : null;
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
          const viaToken = !!process.env.CLOUDFLARE_API_TOKEN;
          const r = await run(["whoami"]);
          const state = parseWhoami(r.out, viaToken);
          if (r.code !== 0 || (!state.signedIn && !viaToken)) {
            return fail("You are not signed in to Cloudflare.", "Click Sign in with Cloudflare (or run `oc login`). Your browser opens Cloudflare's own page; click Allow.", "sign-in");
          }
          const metrics = [
            ...(state.email ? [{ label: "signed in as", value: state.email }] : []),
            ...(state.accounts.length ? [{ label: "account", value: state.accounts.join(", ") }] : []),
            { label: "access granted", value: viaToken ? "an API token from the environment (Advanced / CI)" : state.scopes.join(", ") || "none listed" },
          ];
          if (state.missing.length > 0) {
            return { ok: false, detail: `The sign-in is missing access for: ${state.missing.join(", ")}.`, metrics, fix: { text: "Sign in with Cloudflare again and click Allow on every item.", action: "sign-in" } };
          }
          return { ok: true, detail: viaToken ? "Using the API token from the environment." : "Signed in with Cloudflare.", metrics: [...metrics, { label: "time", value: ms1(Date.now() - t0) }] };
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
          // The access secret is created once and then kept: a run that finds it leaves it, and the token saved on this computer, alone.
          // Issuing a new one breaks every other holder of the old token (the desktop's keyring, the CI secret, a second computer),
          // so that only happens on an explicit `--rotate-token`.
          let created = false;
          let rotated = false;
          if (have && o.rotateToken) {
            const id = await secretId(sid, SECRET_NAMES.access);
            if (!id) return fail("The access secret's id could not be read.", "Retry.");
            const del = await run(["secrets-store", "secret", "delete", sid, "--secret-id", id, "--remote"]);
            if (del.code !== 0) return fail("The old access secret could not be removed.", "Nothing was changed. Retry.");
            const token = randomBytes(32).toString("hex");
            if (!(await putSecret(sid, SECRET_NAMES.access, token))) {
              return fail("The new access secret could not be stored; the old one is already removed.", "Run setup again: with no secret in the store it creates a new one.");
            }
            writeState({ token }, stateDir);
            rotated = true;
          } else if (!have) {
            const token = randomBytes(32).toString("hex");
            if (!(await putSecret(sid, SECRET_NAMES.access, token))) return fail("The access secret could not be stored.", "Retry.");
            writeState({ token }, stateDir);
            created = true;
          }
          const savedHere = typeof readState(stateDir).token === "string" && readState(stateDir).token !== "";
          const cfg = renderDeployConfig(parseJsonc(readFileSync(path.join(o.cloudDir, "wrangler.jsonc"), "utf8")), { databaseId: db, storeId: sid, workerName: worker, ...(o.gatewayId ? { gatewayId: o.gatewayId } : {}) });
          writeFileSync(deployCfg, JSON.stringify(cfg, null, 2), { mode: 0o600 });
          const mig = await run(["d1", "migrations", "apply", dbName, "--remote", "-c", deployCfg]);
          if (mig.code !== 0) return fail("The database migration failed.", `Retry. Detail: ${scrub(mig.out).split("\n").slice(-3).join(" ").slice(0, 200)}`);
          const detail = created
            ? "Created the database, bucket and access secret, and applied the schema."
            : rotated
              ? "Issued a NEW access token and applied the schema. Update it wherever the old one is used: the OC_CLOUD_TOKEN secret in CI and any other computer (copy the \"token\" from ~/.config/open-computer/cloud.json). The desktop on this computer picks it up by itself."
              : savedHere
                ? "Everything already existed; schema is current. The access token is unchanged."
                : "Everything already existed; schema is current. The access token exists but is not saved on this computer: use the cloud.json of the computer that ran setup first, or run setup with --rotate-token (then CI and other computers need the new token).";
          return {
            ok: true,
            detail,
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
          // The deploy uses this repository's pinned Wrangler when it can (the config needs its version); the configured one otherwise.
          const r = await (o.deployRun ?? run)(["deploy", "-c", deployCfg]);
          if (r.code !== 0) {
            const real = wranglerErrorLines(r.out).join(" ");
            o.log?.(`deploy failed (exit ${r.code}): ${real}`);
            if (dockerProblem(r.out)) {
              return fail("The sandbox image could not be built or pushed.", `Make sure Docker is running (Containers need a local image build), then retry. Wrangler said: ${real}`.slice(0, 600), "retry");
            }
            return fail("The deploy failed.", `Retry. Wrangler said: ${real}`.slice(0, 600));
          }
          const url = /https:\/\/[a-z0-9.-]+\.workers\.dev/i.exec(r.out)?.[0];
          if (url) writeState({ url }, stateDir);
          let note = "";
          const saved = readState(stateDir);
          if (o.probe && url && saved.token && (await o.probe(url, saved.token)) === 401) {
            note = " The token saved on this computer is not the core's: run setup with --rotate-token to issue a new one.";
          }
          return { ok: true, detail: (url ? "Deployed. The core is live on your workers.dev address (protected by its access token)." : "Deployed.") + note, metrics: [{ label: "time", value: ms1(Date.now() - t0) }] };
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
