import type { RiskClass } from "../events.ts";

export interface ToolCall {
  id: string;
  name: string;
  /** Raw JSON text exactly as the model streamed it. */
  arguments: string;
}

/** A replay record: provider-specific reasoning blocks that must be sent back unmodified on the next turn. */
export type ReasoningDetail = Record<string, unknown>;

export type ChatMessage =
  | { role: "system"; content: string }
  | { role: "user"; content: string }
  | { role: "assistant"; content: string | null; tool_calls?: ToolCall[]; reasoning_details?: ReasoningDetail[] }
  | { role: "tool"; tool_call_id: string; content: string };

export interface ToolSpec {
  name: string;
  description: string;
  /** JSON Schema for the arguments object. */
  parameters: Record<string, unknown>;
  risk: RiskClass;
}

export interface ProviderRequest {
  model: string;
  messages: ChatMessage[];
  tools?: ToolSpec[];
  reasoning?: { effort?: "minimal" | "low" | "medium" | "high"; max_tokens?: number };
  maxTokens?: number;
}

export interface Usage {
  prompt: number;
  completion: number;
  reasoning: number;
  cached: number;
  /** Provider-reported cost in dollars, or null when the provider did not report one. */
  cost: number | null;
}

/** What a provider yields while a response streams. Higher layers stamp these into AgentEvents. */
export type StreamPart =
  | { t: "reasoning"; kind: "text" | "summary"; text: string }
  | { t: "text"; text: string }
  | { t: "tool_start"; index: number; call_id: string; name: string }
  | { t: "tool_args"; call_id: string; text: string }
  | { t: "error"; code: string; message: string; retryable: boolean }
  | {
      t: "done";
      message: Extract<ChatMessage, { role: "assistant" }>;
      finishReason: string | null;
      usage: Usage | null;
      reasoningKinds: Array<"text" | "summary">;
    };

export type ProviderErrorCode =
  | "key_invalid"
  | "payment_required"
  | "rate_limited"
  | "provider_http_error"
  | "provider_stream_error";

export class ProviderError extends Error {
  readonly code: ProviderErrorCode;
  readonly status: number | null;
  constructor(code: ProviderErrorCode, message: string, status: number | null = null) {
    super(message);
    this.name = "ProviderError";
    this.code = code;
    this.status = status;
  }
}

export type FetchLike = (input: string, init: RequestInit) => Promise<Response>;

/** A provider turns one request into a stream of parts. */
export type Provider = (request: ProviderRequest, signal?: AbortSignal) => AsyncGenerator<StreamPart>;
