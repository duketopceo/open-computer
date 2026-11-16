import { env as raw } from "cloudflare:workers";
import type { D1Migration } from "cloudflare:test";
import type { Env } from "../src/env.ts";

/** The test environment, typed with this project's bindings plus the migrations the config passes in. */
export const env = raw as unknown as Env & { TEST_MIGRATIONS: D1Migration[] };
