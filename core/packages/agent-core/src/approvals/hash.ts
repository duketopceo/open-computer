/** Stable JSON: object keys sorted recursively so the same arguments always hash the same. */
export function stableStringify(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value) ?? "null";
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(",")}]`;
  const obj = value as Record<string, unknown>;
  return `{${Object.keys(obj)
    .sort()
    .map((k) => `${JSON.stringify(k)}:${stableStringify(obj[k])}`)
    .join(",")}}`;
}

export async function sha256Hex(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** Hash an approval binds to: tool name, normalized arguments and the sandbox the action will run in. */
export async function actionHash(name: string, rawArguments: string, sandboxId: string): Promise<string> {
  let normalized: unknown;
  try {
    normalized = JSON.parse(rawArguments);
  } catch {
    normalized = { __unparsed: rawArguments };
  }
  return sha256Hex(stableStringify({ name, args: normalized, sandbox: sandboxId }));
}

export async function capRaiseHash(taskId: string, amount: number, currentCap: number): Promise<string> {
  return sha256Hex(stableStringify({ kind: "cap_raise", task: taskId, amount, cap: currentCap }));
}
