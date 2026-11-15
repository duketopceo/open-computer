import type { EventBody } from "../events.ts";
import type { ToolSpec } from "../provider/types.ts";

export interface ToolContext {
  taskId: string;
  callId: string;
  signal?: AbortSignal;
  emit: (body: EventBody) => void;
}

export interface Tool {
  spec: ToolSpec;
  /** Returns the text the model sees. Throwing becomes an error observation, never a crash. */
  run(args: unknown, ctx: ToolContext): Promise<string>;
}

/** Reads a required string argument from a tool call, with a message the model can act on. */
export function requireString(args: unknown, key: string): string {
  const v = (args as Record<string, unknown> | null)?.[key];
  if (typeof v !== "string") throw new Error(`"${key}" must be a string.`);
  return v;
}

export class ToolRegistry {
  private readonly tools = new Map<string, Tool>();
  constructor(tools: Tool[] = []) {
    for (const t of tools) this.add(t);
  }
  add(tool: Tool): void {
    this.tools.set(tool.spec.name, tool);
  }
  get(name: string): Tool | undefined {
    return this.tools.get(name);
  }
  specs(): ToolSpec[] {
    return [...this.tools.values()].map((t) => t.spec);
  }
}
