import type { SessionDO } from "./session-do.ts";
import type { LedgerDO } from "./storage/ledger-do.ts";
import type { TaskParams } from "./task-workflow.ts";

export interface Env {
  SESSION: DurableObjectNamespace<SessionDO>;
  LEDGER: DurableObjectNamespace<LedgerDO>;
  /** The sandbox Durable Object (a Container per task). Absent under the test pool, which has no Containers. */
  SANDBOX?: DurableObjectNamespace;
  TASK_WORKFLOW: Workflow<TaskParams>;
  DB: D1Database;
  ARTIFACTS: R2Bucket;
  ACCESS_SECRET: SecretsStoreSecret;
  MODEL_KEY: SecretsStoreSecret;
  DEFAULT_MODEL: string;
  /** How long a task waits for a human decision before it fails as expired, for example "24 hours". */
  APPROVAL_TIMEOUT: string;
  AI_GATEWAY_ACCOUNT: string;
  AI_GATEWAY_ID: string;
}
