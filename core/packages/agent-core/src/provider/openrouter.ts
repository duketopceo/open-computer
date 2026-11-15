import { readSseData } from "./sse.ts";
import {
  ProviderError,
  type ChatMessage,
  type FetchLike,
  type Provider,
  type ProviderRequest,
  type ReasoningDetail,
  type StreamPart,
  type Usage,
} from "./types.ts";

export interface OpenRouterOptions {
  fetch: FetchLike;
  /** Chat Completions base, for example the direct API or an AI Gateway route. No trailing slash needed. */
  baseUrl: string;
  /** Resolves the key per call so a rotated key is picked up; the key is never logged or put in a body. */
  apiKey: () => string | Promise<string>;
  /** Extra non-secret headers (attribution, gateway authorization). */
  headers?: Record<string, string>;
}

export const OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1";

function toWireMessage(m: ChatMessage): Record<string, unknown> {
  if (m.role !== "assistant") return { ...m };
  return {
    role: "assistant",
    content: m.content,
    ...(m.tool_calls
      ? {
          tool_calls: m.tool_calls.map((c) => ({
            id: c.id,
            type: "function",
            function: { name: c.name, arguments: c.arguments },
          })),
        }
      : {}),
    // Replayed unmodified and in order, as OpenRouter requires for multi-turn tool use with reasoning.
    ...(m.reasoning_details ? { reasoning_details: m.reasoning_details } : {}),
  };
}

export function buildRequestBody(request: ProviderRequest): Record<string, unknown> {
  const body: Record<string, unknown> = {
    model: request.model,
    messages: request.messages.map(toWireMessage),
    stream: true,
  };
  if (request.tools && request.tools.length > 0) {
    body.tools = request.tools.map((t) => ({
      type: "function",
      function: { name: t.name, description: t.description, parameters: t.parameters },
    }));
    // A provider that does not support tools would otherwise silently drop them.
    body.provider = { require_parameters: true };
  }
  if (request.reasoning) body.reasoning = request.reasoning;
  if (request.maxTokens !== undefined) body.max_tokens = request.maxTokens;
  return body;
}

function mapHttpError(status: number, text: string, key: string): ProviderError {
  const clean = (key ? text.split(key).join("[redacted]") : text).slice(0, 400);
  if (status === 401 || status === 403) return new ProviderError("key_invalid", clean || "The key was rejected.", status);
  if (status === 402) return new ProviderError("payment_required", clean || "The provider account has no credit.", status);
  if (status === 429) return new ProviderError("rate_limited", clean || "The provider is rate limiting requests.", status);
  return new ProviderError("provider_http_error", clean || `Provider returned HTTP ${status}.`, status);
}

type Json = Record<string, unknown>;

const isObj = (v: unknown): v is Json => typeof v === "object" && v !== null && !Array.isArray(v);
const num = (v: unknown): number => (typeof v === "number" && Number.isFinite(v) ? v : 0);

function parseUsage(raw: unknown): Usage | null {
  if (!isObj(raw)) return null;
  const prompt = isObj(raw.prompt_tokens_details) ? num(raw.prompt_tokens_details.cached_tokens) : 0;
  const reasoning = isObj(raw.completion_tokens_details) ? num(raw.completion_tokens_details.reasoning_tokens) : 0;
  return {
    prompt: num(raw.prompt_tokens),
    completion: num(raw.completion_tokens),
    reasoning,
    cached: prompt,
    cost: typeof raw.cost === "number" ? raw.cost : null,
  };
}

/** Merges streamed fragments of one reasoning block (same type and index); encrypted blocks pass through. */
function pushDetail(details: ReasoningDetail[], item: ReasoningDetail): void {
  const last = details[details.length - 1];
  const field = item.type === "reasoning.text" ? "text" : item.type === "reasoning.summary" ? "summary" : null;
  if (
    last &&
    field &&
    last.type === item.type &&
    last.index === item.index &&
    typeof last[field] === "string" &&
    typeof item[field] === "string"
  ) {
    last[field] = (last[field] as string) + (item[field] as string);
    if (item.signature !== undefined) last.signature = item.signature;
    return;
  }
  details.push({ ...item });
}

export function openRouterProvider(options: OpenRouterOptions): Provider {
  return async function* provider(request, signal) {
    const key = await options.apiKey();
    const url = `${options.baseUrl.replace(/\/+$/, "")}/chat/completions`;
    let response: Response;
    try {
      response = await options.fetch(url, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          authorization: `Bearer ${key}`,
          ...(options.headers ?? {}),
        },
        body: JSON.stringify(buildRequestBody(request)),
        ...(signal ? { signal } : {}),
      });
    } catch (err) {
      if (signal?.aborted) return;
      const msg = err instanceof Error ? err.message : "network error";
      throw new ProviderError("provider_http_error", key ? msg.split(key).join("[redacted]") : msg);
    }
    if (!response.ok) {
      const text = await response.text().catch(() => "");
      throw mapHttpError(response.status, text, key);
    }
    if (!response.body) throw new ProviderError("provider_stream_error", "The provider returned no body.");

    let content = "";
    const details: ReasoningDetail[] = [];
    const kinds = new Set<"text" | "summary">();
    const calls = new Map<number, { id: string; name: string; args: string }>();
    let finish: string | null = null;
    let usage: Usage | null = null;

    try {
      for await (const data of readSseData(response.body)) {
        let chunk: unknown;
        try {
          chunk = JSON.parse(data);
        } catch {
          continue; // a malformed fragment is skipped; the stream's own error chunk is authoritative
        }
        if (!isObj(chunk)) continue;
        if (isObj(chunk.error)) {
          yield {
            t: "error",
            code: "provider_stream_error",
            message: String(chunk.error.message ?? "The provider reported an error mid-stream."),
            retryable: false,
          };
          return;
        }
        if (chunk.usage) usage = parseUsage(chunk.usage) ?? usage;
        const choice = Array.isArray(chunk.choices) && isObj(chunk.choices[0]) ? chunk.choices[0] : null;
        if (!choice) continue;
        if (typeof choice.finish_reason === "string") finish = choice.finish_reason;
        if (choice.finish_reason === "error") {
          yield {
            t: "error",
            code: "provider_stream_error",
            message: "The provider ended the response with an error.",
            retryable: false,
          };
          return;
        }
        const delta = isObj(choice.delta) ? choice.delta : null;
        if (!delta) continue;

        let reasoningEmitted = false;
        if (Array.isArray(delta.reasoning_details)) {
          for (const item of delta.reasoning_details) {
            if (!isObj(item)) continue;
            pushDetail(details, item);
            if (item.type === "reasoning.text" && typeof item.text === "string" && item.text) {
              kinds.add("text");
              reasoningEmitted = true;
              yield { t: "reasoning", kind: "text", text: item.text };
            } else if (item.type === "reasoning.summary") {
              const text = typeof item.summary === "string" ? item.summary : typeof item.text === "string" ? item.text : "";
              if (text) {
                kinds.add("summary");
                reasoningEmitted = true;
                yield { t: "reasoning", kind: "summary", text };
              }
            }
            // reasoning.encrypted: kept in `details` for replay, never emitted publicly.
          }
        }
        const plain =
          typeof delta.reasoning === "string" ? delta.reasoning : typeof delta.reasoning_content === "string" ? delta.reasoning_content : "";
        if (plain && !reasoningEmitted) {
          kinds.add("text");
          yield { t: "reasoning", kind: "text", text: plain };
        }
        if (typeof delta.content === "string" && delta.content) {
          content += delta.content;
          yield { t: "text", text: delta.content };
        }
        if (Array.isArray(delta.tool_calls)) {
          for (const tc of delta.tool_calls) {
            if (!isObj(tc)) continue;
            const index = typeof tc.index === "number" ? tc.index : 0;
            const fn = isObj(tc.function) ? tc.function : {};
            let entry = calls.get(index);
            if (!entry) {
              entry = { id: typeof tc.id === "string" ? tc.id : `call_${index}`, name: "", args: "" };
              calls.set(index, entry);
              if (typeof fn.name === "string") entry.name = fn.name;
              yield { t: "tool_start", index, call_id: entry.id, name: entry.name };
            } else if (typeof fn.name === "string" && !entry.name) {
              entry.name = fn.name;
            }
            if (typeof fn.arguments === "string" && fn.arguments) {
              entry.args += fn.arguments;
              yield { t: "tool_args", call_id: entry.id, text: fn.arguments };
            }
          }
        }
      }
    } catch (err) {
      if (signal?.aborted) return;
      throw err;
    }
    if (signal?.aborted) return;

    if (finish === null || finish === "length") {
      yield {
        t: "error",
        code: "provider_stream_error",
        message:
          finish === "length"
            ? "The model's answer was cut off by its output limit."
            : "The response ended before the model finished.",
        retryable: false,
      };
      return;
    }

    const ordered = [...calls.entries()].sort((a, b) => a[0] - b[0]).map(([, c]) => c);
    const message: Extract<ChatMessage, { role: "assistant" }> = {
      role: "assistant",
      content: content === "" && ordered.length > 0 ? null : content,
      ...(ordered.length > 0
        ? { tool_calls: ordered.map((c) => ({ id: c.id, name: c.name, arguments: c.args })) }
        : {}),
      ...(details.length > 0 ? { reasoning_details: details } : {}),
    };
    const done: StreamPart = { t: "done", message, finishReason: finish, usage, reasoningKinds: [...kinds] };
    yield done;
  };
}
