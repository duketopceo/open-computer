import { timingSafeEqual } from "node:crypto";
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import type { AddressInfo } from "node:net";
import { ProviderError, type FetchLike } from "@open-computer/agent-core";
import defaults from "../../../contracts/defaults.json" with { type: "json" };
import { testConnection } from "./test-connection.ts";
import { readFileSync } from "node:fs";
import { settingRefusal, validateUserDailyCap, parseItPolicy, type SandboxFactory } from "@open-computer/agent-core";
import { Runtime } from "./runtime/runtime.ts";
import { handleRuntimeRoutes } from "./runtime/routes.ts";
import type { LocalStore } from "./runtime/store.ts";

export const VERSION = "0.1.0";
const LOOPBACK = new Set(["127.0.0.1", "::1", "localhost"]);
const SECRET_SLOTS = ["cloud_access", "model_key", "custom_key"] as const;
type SecretSlot = (typeof SECRET_SLOTS)[number];

export interface Settings {
  cloud_url: string | null;
  model: string;
  memory_enabled: boolean;
}

export interface LocalServerOptions {
  /** Bearer token the shell supplies on stdin; every endpoint needs it. */
  token: string;
  fetch?: FetchLike;
  host?: string;
  port?: number;
  checkTimeoutMs?: number;
  settings?: Partial<Settings>;
  /** Turns the server into the full agent runtime (self-hosted daemon or local-only mode). */
  runtime?: {
    store: LocalStore;
    sandbox: SandboxFactory;
    /** Which sandbox backend was chosen: bubblewrap, space, cube or none. */
    sandboxKind: string;
    baseUrl?: string;
    policyJson?: string;
    otlpEndpoint?: string;
    /** File holding the model key (daemon mode); read at start. */
    modelKeyFile?: string;
  };
}

export interface RunningServer {
  port: number;
  server: Server;
  close(): Promise<void>;
}

/** The access secret is sent to this address, so it must be https, or plain http only to this computer. */
export function isAllowedCloudUrl(value: unknown): boolean {
  if (typeof value !== "string") return false;
  try {
    const u = new URL(value);
    return u.protocol === "https:" || (u.protocol === "http:" && LOOPBACK.has(u.hostname));
  } catch {
    return false;
  }
}

function safeEqual(a: string, b: string): boolean {
  const x = Buffer.from(a);
  const y = Buffer.from(b);
  return x.length === y.length && timingSafeEqual(x, y);
}

function send(res: ServerResponse, status: number, body: unknown): void {
  const text = JSON.stringify(body);
  res.writeHead(status, { "content-type": "application/json", "content-length": Buffer.byteLength(text) });
  res.end(text);
}

const err = (res: ServerResponse, status: number, code: string, message: string) =>
  send(res, status, { error: { code, message } });

async function readJson(req: IncomingMessage, limit = 64 * 1024): Promise<unknown> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const c of req) {
    size += (c as Buffer).length;
    if (size > limit) throw new Error("too large");
    chunks.push(c as Buffer);
  }
  return chunks.length ? JSON.parse(Buffer.concat(chunks).toString("utf8")) : {};
}

export async function startLocalServer(options: LocalServerOptions): Promise<RunningServer> {
  const host = options.host ?? "127.0.0.1";
  if (!LOOPBACK.has(host)) throw new Error("The local core only binds to a loopback address.");
  const fetchImpl: FetchLike = options.fetch ?? ((url, init) => fetch(url, init));
  const secrets = new Map<SecretSlot, string>();
  if (options.runtime?.modelKeyFile) {
    try {
      const k = readFileSync(options.runtime.modelKeyFile, "utf8").trim();
      if (k) secrets.set("model_key", k);
    } catch {
      /* no key yet: the wizard reports it */
    }
  }
  const modelKey = (): string | null => secrets.get("model_key") ?? null;
  const runtime = options.runtime
    ? new Runtime({
        store: options.runtime.store,
        fetch: fetchImpl,
        apiKey: () => {
          const k = modelKey();
          if (!k) throw new ProviderError("key_missing", "No model key is stored on this core yet.");
          return k;
        },
        customKey: () => secrets.get("custom_key") ?? null,
        ...(options.runtime.baseUrl ? { baseUrl: options.runtime.baseUrl } : {}),
        sandbox: options.runtime.sandbox,
        ...(options.runtime.policyJson ? { policyJson: options.runtime.policyJson } : {}),
        ...(options.runtime.otlpEndpoint ? { otlpEndpoint: options.runtime.otlpEndpoint } : {}),
      })
    : null;
  let selfBase = "";
  let settings: Settings = { cloud_url: null, model: defaults.default_model, memory_enabled: true, ...options.settings };

  const settingsBody = () => ({
    cloud_url: settings.cloud_url,
    model: settings.model,
    memory_enabled: settings.memory_enabled,
    secrets_set: Object.fromEntries(SECRET_SLOTS.map((slot) => [slot, secrets.has(slot)])),
    ...(runtime ? { daily_cap_usd: runtime.caps().user, effective_daily_cap_usd: runtime.caps().effective, policy_max_daily_cap_usd: runtime.caps().policyMax, ...runtime.userSettings() } : {}),
  });

  const server = createServer(async (req, res) => {
    try {
      const auth = req.headers.authorization ?? "";
      if (!auth.startsWith("Bearer ") || !safeEqual(auth.slice(7), options.token)) {
        return err(res, 401, "unauthorized", "Missing or wrong token.");
      }
      const path = (req.url ?? "").split("?")[0];
      const method = req.method ?? "GET";
      if (method === "GET" && path === "/v1/health") return send(res, 200, { ok: true, version: VERSION });
      if (method === "GET" && path === "/v1/settings") return send(res, 200, settingsBody());
      if (method === "PUT" && path === "/v1/settings") {
        const body = (await readJson(req)) as Record<string, unknown>;
        const allowed = new Set(["cloud_url", "model", "memory_enabled", ...(runtime ? ["daily_cap_usd", "default_model", "worker_model", "custom_endpoint_url", "custom_endpoint_model"] : [])]);
        for (const k of Object.keys(body)) if (!allowed.has(k)) return err(res, 400, "bad_request", `Unknown setting "${k}".`);
        if ("memory_enabled" in body && typeof body.memory_enabled !== "boolean") {
          return err(res, 400, "bad_request", "memory_enabled must be true or false.");
        }
        if ("model" in body && (typeof body.model !== "string" || body.model === "")) {
          return err(res, 400, "bad_request", "model must be a non-empty string.");
        }
        if ("cloud_url" in body && body.cloud_url === "") body.cloud_url = null;
        if ("cloud_url" in body && body.cloud_url !== null && !isAllowedCloudUrl(body.cloud_url)) {
          return err(res, 400, "bad_request", "cloud_url must be an https address, or http on this computer.");
        }
        if (runtime) {
          const policy = parseItPolicy(options.runtime?.policyJson);
          for (const k of Object.keys(body)) {
            const locked = settingRefusal(policy, k);
            if (locked) return err(res, 403, "policy_locked", locked);
          }
        }
        if (runtime) {
          const MODEL = /^[A-Za-z0-9._:/-]{1,100}$/;
          for (const k of ["default_model", "worker_model", "custom_endpoint_model"] as const) {
            if (!(k in body)) continue;
            const v = body[k];
            if (v === null || v === "") runtime.store.setSetting(k, null);
            else if (typeof v === "string" && MODEL.test(v)) runtime.store.setSetting(k, v);
            else return err(res, 400, "bad_request", `${k} must be a model name such as vendor/model.`);
            delete body[k];
          }
          if ("custom_endpoint_url" in body) {
            const v = body.custom_endpoint_url;
            if (v === null || v === "") runtime.store.setSetting("custom_endpoint_url", null);
            else if (isAllowedCloudUrl(v)) runtime.store.setSetting("custom_endpoint_url", String(v).replace(/\/+$/, ""));
            else return err(res, 400, "bad_request", "custom_endpoint_url must be an https address, or http on this computer.");
            delete body.custom_endpoint_url;
          }
        }
        if (runtime && "daily_cap_usd" in body) {
          if (body.daily_cap_usd === null) runtime.store.setSetting("daily_cap_usd", null);
          else {
            const checked = validateUserDailyCap(body.daily_cap_usd);
            if (typeof checked === "string") return err(res, 400, "bad_request", checked);
            runtime.store.setSetting("daily_cap_usd", String(checked));
          }
          delete body.daily_cap_usd;
        }
        settings = { ...settings, ...(body as Partial<Settings>) };
        return send(res, 200, settingsBody());
      }
      if (method === "POST" && path === "/v1/secrets/load") {
        const body = (await readJson(req)) as { secrets?: Record<string, unknown> };
        const loaded: SecretSlot[] = [];
        for (const slot of SECRET_SLOTS) {
          const v = body.secrets?.[slot];
          if (typeof v === "string" && v !== "") {
            secrets.set(slot, v);
            loaded.push(slot);
          }
        }
        return send(res, 200, { loaded });
      }
      if (method === "POST" && path === "/v1/test-connection") {
        // Local-only mode (a runtime and no cloud address): the core checks itself with the same routes.
        const selfCheck = runtime !== null && !settings.cloud_url;
        const result = await testConnection({
          fetch: fetchImpl,
          cloudUrl: selfCheck ? selfBase : settings.cloud_url,
          cloudSecret: selfCheck ? options.token : (secrets.get("cloud_access") ?? null),
          memoryEnabled: settings.memory_enabled,
          timeoutMs: options.checkTimeoutMs ?? 5000,
        });
        return send(res, 200, result);
      }
      if (runtime && options.runtime) {
        const handled = await handleRuntimeRoutes(
          { runtime, version: VERSION, sandboxKind: options.runtime.sandboxKind, modelKey, helpers: { send, err, readJson } },
          req,
          res,
          path ?? "",
          method,
          new URL(req.url ?? "/", "http://local"),
        );
        if (handled) return;
      }
      return err(res, 404, "not_found", "No such endpoint.");
    } catch {
      return err(res, 400, "bad_request", "The request could not be read.");
    }
  });

  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(options.port ?? 0, host, () => resolve());
  });
  const port = (server.address() as AddressInfo).port;
  selfBase = `http://127.0.0.1:${port}`;
  return {
    port,
    server,
    close: () =>
      new Promise<void>((resolve) => {
        runtime?.dispose();
        server.close(() => resolve());
        server.closeAllConnections();
      }),
  };
}
