import type { RiskClass } from "../events.ts";

export type PolicyDecision = "allow" | "ask" | "deny";

export interface Policy {
  decide(risk: RiskClass): PolicyDecision;
}

/** Read-only tools run freely; anything that writes, reaches the network or executes code asks first. */
export function createPolicy(overrides: Partial<Record<RiskClass, PolicyDecision>> = {}): Policy {
  const table: Record<RiskClass, PolicyDecision> = { read: "allow", write: "ask", network: "ask", exec: "ask", ...overrides };
  return { decide: (risk) => table[risk] };
}

export const defaultPolicy: Policy = createPolicy();
