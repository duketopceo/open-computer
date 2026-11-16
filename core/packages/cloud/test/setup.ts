import { applyD1Migrations } from "cloudflare:test";
import { env } from "./env.ts";

// Isolated per-test storage starts empty, so the schema is applied before every test file's tests.
await applyD1Migrations(env.DB, env.TEST_MIGRATIONS);
