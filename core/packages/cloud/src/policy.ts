// The IT policy helpers live in agent-core so the cloud core and the self-hosted daemon enforce the same rules.
export {
  NO_IT_POLICY as NO_POLICY,
  ItPolicyError as PolicyError,
  parseItPolicy as parsePolicy,
  resolveDailyCaps,
  validateUserDailyCap,
} from "@open-computer/agent-core";
export type { DailyCaps, ItPolicy as Policy } from "@open-computer/agent-core";
