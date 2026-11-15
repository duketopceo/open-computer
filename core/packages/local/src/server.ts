import { timingSafeEqual } from "node:crypto";
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import type { AddressInfo } from "node:net";
import type { FetchLike } from "@open-computer/agent-core";
import defaults from "../../../contracts/defaults.json" with { type: "json" };
import { testConnection } from "./test-connection.ts";

export const VERSION = "0.1.0";
const LOOPBACK = new Set(["127.0.0.1", "::1", "localhost"]);
const SECRET_SLOTS = ["cloud_access", "model_key"] as const;
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
  let settings: Settings = { cloud_url: null, model: defaults.default_model, memory_enabled: true, ...options.settings };

  const settingsBody = () => ({
    cloud_url: settings.cloud_url,
    model: settings.model,
    memory_enabled: settings.memory_enabled,
    secrets_set: Object.fromEntries(SECRET_SLOTS.map((slot) => [slot, secrets.has(slot)])),
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
        const allowed = new Set(["cloud_url", "model", "memory_enabled"]);
        for (const k of Object.keys(body)) if (!allowed.has(k)) return err(res, 400, "bad_request", `Unknown setting "${k}".`);
        if ("memory_enabled" in body && typeof body.memory_enabled !== "boolean") {
          return err(res, 400, "bad_request", "memory_enabled must be true or false.");
        }
        if ("model" in body && (typeof body.model !== "string" || body.model === "")) {
          return err(res, 400, "bad_request", "model must be a non-empty string.");
        }
        if ("cloud_url" in body && body.cloud_url !== null && !isAllowedCloudUrl(body.cloud_url)) {
          return err(res, 400, "bad_request", "cloud_url must be an https address, or http on this computer.");
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
        const result = await testConnection({
          fetch: fetchImpl,
          cloudUrl: settings.cloud_url,
          cloudSecret: secrets.get("cloud_access") ?? null,
          memoryEnabled: settings.memory_enabled,
          timeoutMs: options.checkTimeoutMs ?? 5000,
        });
        return send(res, 200, result);
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
  return {
    port,
    server,
    close: () =>
      new Promise<void>((resolve) => {
        server.close(() => resolve());
        server.closeAllConnections();
      }),
  };
}
