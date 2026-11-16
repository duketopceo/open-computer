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
  /** Optional: a Cloudflare API token that may run AI Gateway requests (cf-aig-authorization). Bound only when a gateway is set up. */
  AIG_TOKEN?: SecretsStoreSecret;
  /** Model call limits in milliseconds, as text (Wrangler vars are strings). Unset or invalid means the default. */
  MODEL_CONNECT_TIMEOUT_MS?: string;
  MODEL_IDLE_TIMEOUT_MS?: string;
  MODEL_TOTAL_TIMEOUT_MS?: string;
  /** How long a task waits for a human decision before it fails as expired, for example "24 hours". */
  APPROVAL_TIMEOUT: string;
  /** IT policy as JSON text, for example {"max_daily_cap_usd": 20}. Empty means no policy. */
  POLICY_JSON?: string;
  /** Optional OTLP/HTTP traces endpoint (https). The IT policy's otlp_endpoint takes precedence. */
  OTLP_ENDPOINT?: string;
  /**
   * The Workers AI binding. Bound only when a gateway id is set at deploy time. With it, the gateway URL (account id
   * included) comes from the binding, so no account id is ever written into a config file.
   */
  AI?: Ai;
  AI_GATEWAY_ACCOUNT: string;
  AI_GATEWAY_ID: string;
}
