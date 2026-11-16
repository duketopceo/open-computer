// The chat model for the web console: turns the core's event stream into the list the page shows. Mirrors the desktop
// reducer (desktop/crates/shell-core/src/chat.rs). No DOM here, so it is testable and shared.

export type Item =
  | { kind: "user"; text: string }
  | { kind: "thinking"; text: string; done: boolean }
  | { kind: "assistant"; text: string; done: boolean }
  | { kind: "tool"; callId: string; name: string; risk: string; args: string; status: string; output?: string; ms?: number }
  | { kind: "approval"; id: string; hash: string; summary: string; state: string }
  | { kind: "step"; step: number; prompt: number; completion: number; reasoning: number; cost: number | null }
  | { kind: "note"; text: string };

export interface Cost {
  task: number;
  day: number;
  taskCap: number;
  dayCap: number | null;
}

export class Chat {
  items: Item[] = [];
  status = "";
  taskId: string | null = null;
  cost: Cost | null = null;

  pushUser(text: string): void {
    this.items.push({ kind: "user", text });
    this.status = "running";
    this.taskId = null;
    this.cost = null;
  }

  get busy(): boolean {
    return this.status === "running" || this.status === "waiting";
  }

  get hasReasoning(): boolean {
    return this.items.some((i) => i.kind === "thinking");
  }

  get pendingApproval(): Extract<Item, { kind: "approval" }> | undefined {
    for (let i = this.items.length - 1; i >= 0; i--) {
      const it = this.items[i] as Item;
      if (it.kind === "approval" && it.state === "requested") return it;
    }
    return undefined;
  }

  private tool(callId: string): Extract<Item, { kind: "tool" }> | undefined {
    for (let i = this.items.length - 1; i >= 0; i--) {
      const it = this.items[i] as Item;
      if (it.kind === "tool" && it.callId === callId) return it;
    }
    return undefined;
  }

  /** Applies one event. Unknown event types are ignored, so a newer core never breaks an older page. */
  apply(e: Record<string, any>): void {
    const last = this.items[this.items.length - 1];
    switch (e.type) {
      case "run":
        if (typeof e.task_id === "string") this.taskId = e.task_id;
        if (e.status === "failed" || e.status === "cancelled") this.items.push({ kind: "note", text: e.reason ? `Task ${e.status}: ${e.reason}` : `Task ${e.status}.` });
        this.status = String(e.status);
        break;
      case "reasoning.delta":
        if (last?.kind === "thinking" && !last.done) last.text += String(e.text ?? "");
        else this.items.push({ kind: "thinking", text: String(e.text ?? ""), done: false });
        break;
      case "reasoning.done": {
        const t = [...this.items].reverse().find((i) => i.kind === "thinking" && !i.done);
        if (t && t.kind === "thinking") t.done = true;
        break;
      }
      case "message.delta":
        if (last?.kind === "assistant" && !last.done) last.text += String(e.text ?? "");
        else this.items.push({ kind: "assistant", text: String(e.text ?? ""), done: false });
        break;
      case "message.done": {
        const a = [...this.items].reverse().find((i) => i.kind === "assistant" && !i.done);
        if (a && a.kind === "assistant") {
          if (e.text) a.text = String(e.text);
          a.done = true;
        } else if (e.text) this.items.push({ kind: "assistant", text: String(e.text), done: true });
        break;
      }
      case "tool_call.started":
        this.items.push({ kind: "tool", callId: String(e.call_id), name: String(e.name), risk: "", args: "", status: "running" });
        break;
      case "tool_call.args.delta": {
        const t = this.tool(String(e.call_id));
        if (t) t.args += String(e.text ?? "");
        break;
      }
      case "tool_call.finished": {
        const t = this.tool(String(e.call_id));
        if (t) Object.assign(t, { name: String(e.name), args: String(e.args ?? ""), risk: String(e.risk ?? "") });
        else this.items.push({ kind: "tool", callId: String(e.call_id), name: String(e.name), risk: String(e.risk ?? ""), args: String(e.args ?? ""), status: "running" });
        break;
      }
      case "tool_call.result": {
        const t = this.tool(String(e.call_id));
        if (t) Object.assign(t, { status: String(e.status), output: String(e.output ?? ""), ms: typeof e.ms === "number" ? e.ms : undefined });
        break;
      }
      case "approval": {
        const existing = this.items.find((i) => i.kind === "approval" && i.id === e.approval_id);
        if (existing && existing.kind === "approval") existing.state = String(e.state);
        else this.items.push({ kind: "approval", id: String(e.approval_id), hash: String(e.hash), summary: String(e.summary ?? ""), state: String(e.state) });
        break;
      }
      case "usage": {
        const item: Item = { kind: "step", step: Number(e.step), prompt: Number(e.prompt ?? 0), completion: Number(e.completion ?? 0), reasoning: Number(e.reasoning ?? 0), cost: typeof e.cost === "number" ? e.cost : null };
        const at = this.items.findIndex((i) => i.kind === "step" && i.step === item.step);
        if (at >= 0) this.items[at] = item;
        else this.items.push(item);
        break;
      }
      case "cost":
        this.cost = { task: Number(e.task_total ?? 0), day: Number(e.day_total ?? 0), taskCap: Number(e.task_cap ?? 0), dayCap: typeof e.day_cap === "number" ? e.day_cap : null };
        break;
      case "memory_recall":
        if (Array.isArray(e.items) && e.items.length > 0) this.items.push({ kind: "note", text: `Recalled ${e.items.length} note${e.items.length === 1 ? "" : "s"} from memory.` });
        break;
      case "memory_write":
        this.items.push({ kind: "note", text: "Saved a summary to memory." });
        break;
      case "error":
        this.items.push({ kind: "note", text: String(e.message ?? "An error occurred.") });
        break;
      default:
        break;
    }
  }
}

/** A diff-style view of a file write (every new line marked with a plus), or null for any other tool. */
export function writeDiff(args: string): string | null {
  try {
    const v = JSON.parse(args) as { path?: unknown; content?: unknown };
    if (typeof v.path !== "string" || typeof v.content !== "string") return null;
    return [`+++ ${v.path}`, ...v.content.split("\n").slice(0, 40).map((l) => `+ ${l}`)].join("\n");
  } catch {
    return null;
  }
}

/** Reads a server-sent event stream and calls `onEvent` for each JSON data line, until the stream ends. */
export async function readSse(res: Response, onEvent: (e: Record<string, any>) => void): Promise<void> {
  if (!res.body) return;
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (value) buf += dec.decode(value, { stream: true });
    const parts = buf.split("\n\n");
    buf = parts.pop() ?? "";
    for (const part of parts) {
      const line = part.split("\n").find((l) => l.startsWith("data:"));
      if (!line) continue;
      try {
        onEvent(JSON.parse(line.slice(5).trim()) as Record<string, any>);
      } catch {
        /* a malformed frame is skipped */
      }
    }
    if (done) break;
  }
}
