import { requireString, type Tool } from "../agent/tools.ts";
import type { CostGuard } from "../cost/guard.ts";
import { OPENROUTER_BASE_URL } from "../provider/openrouter.ts";
import type { FetchLike } from "../provider/types.ts";

export const DEFAULT_SEARCH_MODEL = "perplexity/sonar";
/** Reserved before each search; settled to the provider-reported cost. */
const SEARCH_ESTIMATE_USD = 0.02;

export interface WebSearchOptions {
  fetch: FetchLike;
  /** Chat Completions base; a direct OpenRouter URL or an AI Gateway route. */
  baseUrl?: string;
  apiKey: () => string | Promise<string>;
  guard: CostGuard;
  model?: string;
  /** Longest answer returned to the model, in characters. */
  maxChars?: number;
}

interface Citation {
  url: string;
  title?: string;
}

function citationsOf(body: Record<string, unknown>): Citation[] {
  const out = new Map<string, Citation>();
  const top = body.citations;
  if (Array.isArray(top)) for (const c of top) if (typeof c === "string") out.set(c, { url: c });
  const message = (body.choices as Array<{ message?: { annotations?: unknown } }> | undefined)?.[0]?.message;
  if (Array.isArray(message?.annotations)) {
    for (const a of message.annotations as Array<{ type?: string; url_citation?: { url?: string; title?: string } }>) {
      const u = a.type === "url_citation" ? a.url_citation : undefined;
      if (u?.url) out.set(u.url, { url: u.url, ...(u.title ? { title: u.title } : {}) });
    }
  }
  return [...out.values()];
}

/**
 * Web search through Perplexity Sonar on the user's own OpenRouter key. Network risk class, so the default policy asks
 * before the first use. The cost counts toward the task and daily caps like any model step. Citations come back with the
 * answer, one numbered source per line, so the model can quote them and memory can store them as a source.
 */
export function webSearchTool(o: WebSearchOptions): Tool {
  const base = (o.baseUrl ?? OPENROUTER_BASE_URL).replace(/\/+$/, "");
  const maxChars = o.maxChars ?? 6000;
  return {
    spec: {
      name: "web.search",
      description: "Search the web and get a short cited answer. Use it for facts that may have changed or that you do not know.",
      parameters: { type: "object", properties: { query: { type: "string", description: "What to look up, in plain words." } }, required: ["query"] },
      risk: "network",
    },
    async run(args, ctx) {
      const query = requireString(args, "query").trim();
      if (query === "" || query.length > 2000) throw new Error('"query" must be 1 to 2000 characters.');
      const stepId = `search:${ctx.taskId}:${ctx.callId}`;
      const reserved = await o.guard.reserve(ctx.taskId, stepId, SEARCH_ESTIMATE_USD);
      if (!reserved.ok) throw new Error(`The ${reserved.scope} spend cap is reached, so the search was not made.`);
      let key: string;
      try {
        key = await o.apiKey();
      } catch {
        await o.guard.settle(stepId, 0);
        throw new Error("No model key is stored, so search is unavailable.");
      }
      let res: Response;
      try {
        res = await o.fetch(`${base}/chat/completions`, {
          method: "POST",
          headers: { authorization: `Bearer ${key}`, "content-type": "application/json" },
          body: JSON.stringify({ model: o.model ?? DEFAULT_SEARCH_MODEL, messages: [{ role: "user", content: query }], stream: false }),
          signal: ctx.signal ? AbortSignal.any([ctx.signal, AbortSignal.timeout(60_000)]) : AbortSignal.timeout(60_000),
        });
      } catch {
        await o.guard.settle(stepId, 0);
        throw new Error("The search service could not be reached.");
      }
      if (!res.ok) {
        await o.guard.settle(stepId, 0);
        const why = res.status === 401 || res.status === 403 ? "The model key was rejected." : res.status === 402 ? "The model account has no credit." : res.status === 429 ? "The search service is rate limiting this key." : `The search service answered with an error (${res.status}).`;
        throw new Error(why);
      }
      const body = (await res.json().catch(() => null)) as Record<string, unknown> | null;
      const content = (body?.choices as Array<{ message?: { content?: unknown } }> | undefined)?.[0]?.message?.content;
      const cost = (body?.usage as { cost?: unknown } | undefined)?.cost;
      await o.guard.settle(stepId, typeof cost === "number" ? cost : null);
      if (!body || typeof content !== "string" || content === "") throw new Error("The search returned no answer.");
      const cites = citationsOf(body);
      const answer = content.length > maxChars ? `${content.slice(0, maxChars)}\n[answer cut at ${maxChars} characters]` : content;
      const sources = cites.length ? `\n\nSources:\n${cites.map((c, i) => `${i + 1}. ${c.title ? `${c.title} ` : ""}${c.url}`).join("\n")}` : "\n\nSources: none returned.";
      return answer + sources;
    },
  };
}
