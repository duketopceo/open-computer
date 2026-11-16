import { applyD1Migrations } from "cloudflare:test";
import { env } from "./env.ts";

// Isolated per-test storage starts empty, so the schema is applied before every test file's tests.
await applyD1Migrations(env.DB, env.TEST_MIGRATIONS);

// The core loads @openrouter/sdk only when an account route needs it, so the Worker starts fast. The test pool loads that
// package module by module, which takes seconds the first time, so it is loaded here, before any test's own timeout starts.
await Promise.all([import("@openrouter/sdk/sdk/sdk.js"), import("@openrouter/sdk/lib/http.js")]);
