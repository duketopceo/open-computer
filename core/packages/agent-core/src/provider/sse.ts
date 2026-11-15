/**
 * Minimal Server-Sent Events reader. Yields the `data` payload of each event, skips comment lines
 * (such as `: OPENROUTER PROCESSING`) and stops at the `[DONE]` marker.
 */
export async function* readSseData(body: ReadableStream<Uint8Array>): AsyncGenerator<string> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let dataLines: string[] = [];
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (value) buffer += decoder.decode(value, { stream: true });
      if (done) buffer += decoder.decode();
      let start = 0;
      let nl: number;
      while ((nl = buffer.indexOf("\n", start)) >= 0) {
        let line = buffer.slice(start, nl);
        start = nl + 1;
        if (line.endsWith("\r")) line = line.slice(0, -1);
        if (line === "") {
          if (dataLines.length > 0) {
            const payload = dataLines.join("\n");
            dataLines = [];
            if (payload === "[DONE]") return;
            yield payload;
          }
          continue;
        }
        if (line.startsWith(":")) continue;
        if (line.startsWith("data:")) {
          dataLines.push(line.slice(5).replace(/^ /, ""));
        }
      }
      buffer = buffer.slice(start);
      if (done) {
        if (dataLines.length > 0) {
          const payload = dataLines.join("\n");
          if (payload !== "[DONE]") yield payload;
        }
        return;
      }
    }
  } finally {
    reader.releaseLock();
  }
}
