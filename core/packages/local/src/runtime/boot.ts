import { spawnSync } from "node:child_process";
import { mkdirSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";
import type { LocalServerOptions } from "../server.ts";
import { sandboxFactoryFor, type SandboxKind } from "./sandbox.ts";
import { LocalStore } from "./store.ts";

const KINDS: SandboxKind[] = ["bubblewrap", "space", "cube", "stub", "none"];

export function defaultStateDir(env: Record<string, string | undefined>): string {
  return env.OC_CORE_STATE_DIR ?? path.join(env.XDG_DATA_HOME ?? path.join(homedir(), ".local", "share"), "open-computer", "core");
}

function hasBwrap(): boolean {
  try {
    return spawnSync("bwrap", ["--version"], { stdio: "ignore" }).status === 0;
  } catch {
    return false;
  }
}

/** Which sandbox to use: OC_SANDBOX when set (and valid), else bubblewrap when installed, else none. */
export function chooseSandbox(env: Record<string, string | undefined>, bwrapPresent: () => boolean = hasBwrap): SandboxKind {
  const asked = env.OC_SANDBOX;
  if (asked !== undefined && asked !== "") {
    if (!KINDS.includes(asked as SandboxKind)) throw new Error(`OC_SANDBOX must be one of ${KINDS.join(", ")}.`);
    return asked as SandboxKind;
  }
  return bwrapPresent() ? "bubblewrap" : "none";
}

/** The runtime half of the server options, built from the environment the daemon or the desktop shell sets. */
export function runtimeFromEnv(env: Record<string, string | undefined>): NonNullable<LocalServerOptions["runtime"]> {
  const dir = defaultStateDir(env);
  mkdirSync(dir, { recursive: true, mode: 0o700 });
  const kind = chooseSandbox(env);
  let policyJson = env.OC_POLICY_JSON;
  if (!policyJson && env.OC_POLICY_FILE) policyJson = readFileSync(env.OC_POLICY_FILE, "utf8");
  return {
    store: new LocalStore(path.join(dir, "core.db")),
    sandbox: sandboxFactoryFor({ kind, env }),
    sandboxKind: kind,
    ...(env.OC_CORE_MODEL_KEY_FILE ? { modelKeyFile: env.OC_CORE_MODEL_KEY_FILE } : {}),
    ...(policyJson ? { policyJson } : {}),
    ...(env.OC_PROFILE_FILE ? { profileJson: readFileSync(env.OC_PROFILE_FILE, "utf8") } : {}),
    ...(env.OC_OTLP_ENDPOINT ? { otlpEndpoint: env.OC_OTLP_ENDPOINT } : {}),
  };
}
