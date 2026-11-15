import type { FetchLike } from "@open-computer/agent-core";
import { MESSAGES, type MessageCode } from "./messages.ts";

export interface PieceResult {
  ok: boolean;
  code: "ok" | MessageCode;
  message: string;
}

export interface TestConnectionResult {
  all_ok: boolean;
  backend: PieceResult;
  model: PieceResult;
  memory: PieceResult;
}

export interface TestConnectionInput {
  fetch: FetchLike;
  cloudUrl: string | null;
  cloudSecret: string | null;
  memoryEnabled: boolean;
  timeoutMs: number;
}

const ok = (message: string): PieceResult => ({ ok: true, code: "ok", message });
const fail = (code: MessageCode): PieceResult => ({ ok: false, code, message: MESSAGES[code] });
const transportFailure = (kind: "timeout" | "unreachable"): PieceResult =>
  fail(kind === "timeout" ? "backend_timeout" : "backend_unreachable");

async function call(
  input: TestConnectionInput,
  path: string,
  method: "GET" | "POST",
): Promise<{ status: number; body: Record<string, unknown> } | { error: "unreachable" | "timeout" }> {
  try {
    const res = await input.fetch(`${(input.cloudUrl ?? "").replace(/\/+$/, "")}${path}`, {
      method,
      headers: { authorization: `Bearer ${input.cloudSecret ?? ""}`, "content-type": "application/json" },
      signal: AbortSignal.timeout(input.timeoutMs),
    });
    let body: Record<string, unknown> = {};
    try {
      body = (await res.json()) as Record<string, unknown>;
    } catch {
      /* a non-JSON body is treated as an empty one */
    }
    return { status: res.status, body };
  } catch (err) {
    const name = err instanceof Error ? err.name : "";
    return { error: name === "TimeoutError" || name === "AbortError" ? "timeout" : "unreachable" };
  }
}

const MODEL_CODES: Record<string, MessageCode> = {
  key_missing: "model_key_missing",
  key_invalid: "model_key_rejected",
  payment_required: "model_no_credit",
  rate_limited: "model_rate_limited",
};

/**
 * Three independent checks, always all three returned so a failing piece is named while the others still
 * report their own state. The model key is checked by the cloud core, because that is the key tasks will use.
 */
export async function testConnection(input: TestConnectionInput): Promise<TestConnectionResult> {
  let backend: PieceResult;
  if (!input.cloudUrl) backend = fail("no_cloud_url");
  else if (!input.cloudSecret) backend = fail("no_cloud_secret");
  else {
    const r = await call(input, "/v1/health", "GET");
    if ("error" in r) backend = transportFailure(r.error);
    else if (r.status === 401 || r.status === 403) backend = fail("backend_unauthorized");
    else if (r.status >= 200 && r.status < 300) backend = ok(MESSAGES.ok_backend);
    else backend = fail("backend_error");
  }

  if (!backend.ok) {
    const blocked = fail("blocked_by_backend");
    const memory = input.memoryEnabled ? blocked : ok(MESSAGES.memory_off);
    return { all_ok: false, backend, model: blocked, memory };
  }

  let model: PieceResult;
  const m = await call(input, "/v1/model/health", "GET");
  if ("error" in m) model = transportFailure(m.error);
  else if (m.status === 200 && m.body.ok === true) model = ok(MESSAGES.ok_model);
  else model = fail(MODEL_CODES[String(m.body.code ?? "")] ?? "model_error");

  let memory: PieceResult;
  if (!input.memoryEnabled) memory = ok(MESSAGES.memory_off);
  else {
    const p = await call(input, "/v1/memory/probe", "POST");
    memory = !("error" in p) && p.status === 200 && p.body.ok === true ? ok(MESSAGES.ok_memory) : fail("memory_failed");
  }

  return { all_ok: backend.ok && model.ok && memory.ok, backend, model, memory };
}
