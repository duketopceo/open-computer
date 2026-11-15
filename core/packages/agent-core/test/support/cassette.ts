import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import type { FetchLike } from "../../src/provider/types.ts";

export function loadCassette(name: string): string {
  return readFileSync(fileURLToPath(new URL(`../fixtures/openrouter/${name}.sse`, import.meta.url)), "utf8");
}

export interface RecordedCall {
  url: string;
  init: RequestInit;
  body: Record<string, unknown>;
}

export interface Player {
  fetch: FetchLike;
  calls: RecordedCall[];
}

/** Replays cassettes in order, one per request, splitting bytes into small odd-sized chunks. No network. */
export function cassettePlayer(
  responses: Array<{ cassette: string; status?: number; hangAfterChunks?: number } | { status: number; text: string }>,
  chunkSize = 23,
): Player {
  const calls: RecordedCall[] = [];
  let i = 0;
  const fetchImpl: FetchLike = async (url, init) => {
    calls.push({ url, init, body: JSON.parse(String(init.body ?? "{}")) as Record<string, unknown> });
    const spec = responses[Math.min(i, responses.length - 1)];
    i += 1;
    if (!spec) throw new Error("cassette player has no response configured");
    if ("text" in spec) return new Response(spec.text, { status: spec.status });
    const bytes = new TextEncoder().encode(loadCassette(spec.cassette));
    const signal = init.signal ?? undefined;
    let offset = 0;
    let chunks = 0;
    const stream = new ReadableStream<Uint8Array>({
      async pull(controller) {
        if (spec.hangAfterChunks !== undefined && chunks >= spec.hangAfterChunks) {
          await new Promise<void>((resolve) => {
            if (signal?.aborted) return resolve();
            signal?.addEventListener("abort", () => resolve(), { once: true });
          });
          controller.error(new DOMException("aborted", "AbortError"));
          return;
        }
        if (offset >= bytes.length) {
          controller.close();
          return;
        }
        controller.enqueue(bytes.slice(offset, offset + chunkSize));
        offset += chunkSize;
        chunks += 1;
      },
    });
    return new Response(stream, { status: spec.status ?? 200, headers: { "content-type": "text/event-stream" } });
  };
  return { fetch: fetchImpl, calls };
}
