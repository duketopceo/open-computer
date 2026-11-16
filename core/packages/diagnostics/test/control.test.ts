import { mkdirSync, mkdtempSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { createControlPlane, dockerProblem, readState, scrub, stripAnsi, withFallback, wranglerErrorLines, writeState, type Runner } from "../src/control.ts";
import { extraKeysFromEnv } from "../src/cli.ts";

const STORE = "a".repeat(32);
const SIGNED_IN = "You are logged in with an OAuth Token, associated with the email me@example.com.\nScope (Access)\n- user (read)\n- account (read)\n- workers (write)\n- workers_scripts (write)\n- workers_routes (write)\n- workers_tail (read)\n- d1 (write)\n- secrets_store (write)\n- containers (write)\n- cloudchamber (write)\n";

function fakeWrangler(opts: { secrets?: string[]; whoami?: string; failOn?: string } = {}) {
  const calls: Array<{ args: string[]; input?: string }> = [];
  const secrets = new Set(opts.secrets ?? []);
  const run: Runner = async (args, o) => {
    calls.push({ args, ...(o?.input !== undefined ? { input: o.input } : {}) });
    const key = args.slice(0, 3).join(" ");
    if (opts.failOn && key.startsWith(opts.failOn)) return { code: 1, out: "boom" };
    if (key === "secrets-store store list") return { code: 0, out: `│ default_secrets_store │ ${STORE} │` };
    if (key === "secrets-store secret list") return { code: 0, out: [...secrets].map((n) => `│ ${n} │ id │`).join("\n") };
    if (key === "secrets-store secret create") {
      secrets.add(args[args.indexOf("--name") + 1] as string);
      return { code: 0, out: "created" };
    }
    if (args[0] === "whoami") return { code: 0, out: opts.whoami ?? SIGNED_IN };
    return { code: 0, out: "" };
  };
  return { run, calls, secrets };
}

const make = (run: Runner, extra: Partial<Parameters<typeof createControlPlane>[0]> = {}) =>
  createControlPlane({ run, cloudDir: "/nonexistent", stateDir: mkdtempSync(path.join(tmpdir(), "oc-state-")), ...extra });

describe("keys step", () => {
  it("stores the model key only on stdin, never in arguments, and never prints it", async () => {
    const w = fakeWrangler();
    const secret = ["sk", "test", "control1234567890"].join("-");
    const out = await make(w.run, { modelKey: () => secret }).run("keys");
    expect(out.ok).toBe(true);
    expect(JSON.stringify(out)).not.toContain(secret);
    const create = w.calls.find((c) => c.args.slice(0, 3).join(" ") === "secrets-store secret create");
    expect(create?.input).toBe(secret);
    expect(create?.args.join(" ")).not.toContain(secret);
  });

  it("is idempotent: an existing key is left alone", async () => {
    const w = fakeWrangler({ secrets: ["oc_model_key"] });
    const out = await make(w.run, { modelKey: () => "x" }).run("keys");
    expect(out.ok).toBe(true);
    expect(w.calls.some((c) => c.args.slice(0, 3).join(" ") === "secrets-store secret create")).toBe(false);
  });

  it("stores extra key slots as oc_key_<name>, skipping slots that exist, and names them without values", async () => {
    const w = fakeWrangler({ secrets: ["oc_model_key", "oc_key_search"] });
    const out = await make(w.run, { extraKeys: () => ({ search: "s1", coding: "c1" }) }).run("keys");
    expect(out.ok).toBe(true);
    expect(out.detail).toContain("Extra slots stored: coding");
    expect(JSON.stringify(out)).not.toContain("c1\"");
    expect([...w.secrets]).toContain("oc_key_coding");
  });

  it("rejects a bad slot name before storing anything", async () => {
    const w = fakeWrangler({ secrets: ["oc_model_key"] });
    const out = await make(w.run, { extraKeys: () => ({ "Bad Name": "v" }) }).run("keys");
    expect(out.ok).toBe(false);
    expect(w.calls.some((c) => c.args.slice(0, 3).join(" ") === "secrets-store secret create")).toBe(false);
  });

  it("asks for the key through the environment when none is stored or given", async () => {
    const out = await make(fakeWrangler().run).run("keys");
    expect(out.ok).toBe(false);
    expect(out.fix?.text).toContain("OC_OPENROUTER_KEY");
  });
});

describe("account step", () => {
  it("passes with a full login and fails with the missing permissions named", async () => {
    expect((await make(fakeWrangler().run).run("account")).ok).toBe(true);
    const bad = await make(fakeWrangler({ whoami: "You are logged in with an OAuth Token.\n- workers (write)\n" }).run).run("account");
    expect(bad.ok).toBe(false);
    expect(bad.detail).toContain("d1");
  });
});

describe("features step", () => {
  it("names what to enable when Containers is not available", async () => {
    const w = fakeWrangler({ failOn: "containers list" });
    const out = await make(w.run).run("features");
    expect(out.ok).toBe(false);
    expect(out.fix?.text).toContain("Workers Paid");
    expect(out.metrics?.find((m) => m.label === "Containers")?.value).toBe("not available");
  });
});

describe("helpers", () => {
  it("scrubs ids and key-shaped text from tool output", () => {
    expect(scrub(`id ${STORE} and sk-or-v1-abcdefghijklmnop1234`)).toBe("id <id> and <redacted>");
  });

  it("writes state with mode 0600 and merges patches", () => {
    const dir = mkdtempSync(path.join(tmpdir(), "oc-state-"));
    writeState({ url: "https://core.example" }, dir);
    writeState({ token: "t" }, dir);
    expect(readState(dir)).toEqual({ url: "https://core.example", token: "t" });
    expect(statSync(path.join(dir, "cloud.json")).mode & 0o777).toBe(0o600);
    expect(readFileSync(path.join(dir, "cloud.json"), "utf8")).toContain("core.example");
  });

  it("reads extra key slots from OC_EXTRA_KEY_<NAME> variables", () => {
    expect(extraKeysFromEnv({ OC_EXTRA_KEY_SEARCH: "a", OC_EXTRA_KEY_CODING_AGENT: "b", OTHER: "c", OC_EXTRA_KEY_EMPTY: "" })).toEqual({ search: "a", coding_agent: "b" });
  });
});


const fixture = (name: string) => readFileSync(new URL(`./fixtures/${name}`, import.meta.url), "utf8");

/** A deploy step ready to run: a cloud directory holding the rendered deploy config, and a runner that answers `deploy`. */
function deployStep(deploy: { code: number; out: string }, extra: Partial<Parameters<typeof createControlPlane>[0]> = {}) {
  const cloudDir = mkdtempSync(path.join(tmpdir(), "oc-cloud-"));
  writeFileSync(path.join(cloudDir, "wrangler.deploy.jsonc"), "{}");
  const stateDir = mkdtempSync(path.join(tmpdir(), "oc-state-"));
  const calls: string[][] = [];
  const run: Runner = async (args) => {
    calls.push(args);
    if (args[0] === "deploy") return deploy;
    if (args.slice(0, 3).join(" ") === "secrets-store store list") return { code: 0, out: `│ default_secrets_store │ ${STORE} │` };
    if (args.slice(0, 3).join(" ") === "secrets-store secret list") return { code: 0, out: "│ oc_model_key │ id │" };
    return { code: 0, out: "" };
  };
  return { control: createControlPlane({ run, cloudDir, stateDir, ...extra }), calls, stateDir };
}

describe("deploy step against real Wrangler output", () => {
  it("passes on a deploy that ends with docker's trailing Untagged line, and keeps the workers.dev address", async () => {
    // wrangler-deploy-success.txt: real Wrangler 4.162 output with the deploy lines of a live run (version abb3c2ed) and the
    // "Untagged:" line docker prints last.
    const text = fixture("wrangler-deploy-success.txt");
    expect(text.trimEnd().split("\n").at(-1)).toMatch(/^Untagged: /);
    const { control, stateDir } = deployStep({ code: 0, out: text });
    const out = await control.run("deploy");
    expect(out.ok).toBe(true);
    expect(readState(stateDir).url).toBe("https://open-computer-core.example-account.workers.dev");
  });

  it("when Docker is not available shows Wrangler's own error lines, readable, with the Docker fix", async () => {
    const text = fixture("wrangler-deploy-docker-down.txt");
    expect(text).toContain("\u001b["); // real Wrangler output carries colour codes even with NO_COLOR
    const { control } = deployStep({ code: 1, out: text });
    const out = await control.run("deploy");
    expect(out.ok).toBe(false);
    expect(out.detail).toBe("The sandbox image could not be built or pushed.");
    const fix = out.fix?.text ?? "";
    expect(fix).toContain("Wrangler said:");
    expect(fix).toContain("The Docker CLI is needed to build the configured image");
    expect(fix).not.toContain("\u001b");
  });

  it("any other failure shows Wrangler's real error lines instead of guessing Docker", async () => {
    const out = "\u001b[31m✘ [ERROR]\u001b[0m A request to the Cloudflare API (/accounts/<id>/workers/scripts/open-computer-core) failed.\n  Authentication error [code: 10000]\n#7 CACHED\n#8 CACHED\n";
    const { control } = deployStep({ code: 1, out });
    const res = await control.run("deploy");
    expect(res.ok).toBe(false);
    expect(res.detail).toBe("The deploy failed.");
    expect(res.fix?.text).toContain("Authentication error [code: 10000]");
    expect(res.fix?.text).not.toContain("CACHED");
  });

  it("a success that mentions docker is never read as a failure, and a failure that does not is never read as Docker's", () => {
    expect(dockerProblem(fixture("wrangler-deploy-success.txt"))).toBe(false);
    expect(dockerProblem(fixture("wrangler-deploy-docker-down.txt"))).toBe(true);
    expect(dockerProblem("✘ [ERROR] Authentication error")).toBe(false);
  });

  it("picks the error block out of Wrangler's output without the build progress, the ruler or the telemetry notice", () => {
    const lines = wranglerErrorLines(fixture("wrangler-deploy-docker-down.txt"));
    expect(lines[0]).toContain("[ERROR] The Docker CLI is needed");
    expect(lines.join("\n")).not.toMatch(/telemetry|─|wrangler 4\./);
    expect(stripAnsi("\u001b[31mred\u001b[0m")).toBe("red");
  });

  it("deploys with the repository's own Wrangler when it can start, and with the configured one when it cannot", async () => {
    const seen: string[] = [];
    const primary: Runner = async () => (seen.push("project"), { code: 127, out: "wrangler could not be started" });
    const fallback: Runner = async () => (seen.push("configured"), { code: 0, out: "ok" });
    expect((await withFallback(primary, fallback)(["deploy"])).code).toBe(0);
    expect(seen).toEqual(["project", "configured"]);
    const good: Runner = async () => (seen.push("project-ok"), { code: 1, out: "a real failure" });
    expect((await withFallback(good, fallback)(["deploy"])).code).toBe(1);
    expect(seen.at(-1)).toBe("project-ok");
  });

  it("uses the deploy runner it is given for the deploy and the normal one for everything else", async () => {
    const used: string[] = [];
    const deployRun: Runner = async (args) => (used.push(`deploy:${args[0]}`), { code: 0, out: "Deployed https://x.workers.dev" });
    const { control, calls } = deployStep({ code: 1, out: "never used" }, { deployRun });
    expect((await control.run("deploy")).ok).toBe(true);
    expect(used).toEqual(["deploy:deploy"]);
    expect(calls.some((c) => c[0] === "deploy")).toBe(false);
    mkdirSync(tmpdir(), { recursive: true });
  });
});
