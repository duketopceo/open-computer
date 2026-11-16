import type { RiskClass } from "../events.ts";

export type PolicyDecision = "allow" | "ask" | "deny";

export interface Policy {
  decide(risk: RiskClass, toolName?: string): PolicyDecision;
}

/** Read-only tools run freely; anything that writes, reaches the network or executes code asks first. */
export function createPolicy(overrides: Partial<Record<RiskClass, PolicyDecision>> = {}, denyTools: string[] = []): Policy {
  const table: Record<RiskClass, PolicyDecision> = { read: "allow", write: "ask", network: "ask", exec: "ask", ...overrides };
  return { decide: (risk, toolName) => (toolName !== undefined && denyTools.includes(toolName) ? "deny" : table[risk]) };
}

export const defaultPolicy: Policy = createPolicy();
