// Configuration checks that need the real filesystem, so they run under Node rather than the Workers test pool.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const read = (rel) => readFileSync(fileURLToPath(new URL(`../${rel}`, import.meta.url)), "utf8");
// wrangler.jsonc here uses only whole-line comments, which keeps this stripper honest.
const jsonc = (rel) => {
  const text = read(rel);
  const lines = text.split("\n");
  for (const l of lines) assert.ok(!/\S\s+\/\/\s/.test(l.replace(/"[^"]*"/g, '""')), `trailing comment in ${rel}: ${l}`);
  return JSON.parse(lines.filter((l) => !l.trim().startsWith("//")).join("\n"));
};

const prod = jsonc("wrangler.jsonc");
const testCfg = jsonc("wrangler.test.jsonc");

test("declares the container class, the image and the Durable Object binding for it", () => {
  assert.equal(prod.containers.length, 1);
  assert.equal(prod.containers[0].class_name, "Sandbox");
  assert.equal(prod.containers[0].image, "./sandbox/Dockerfile");
  const binding = prod.durable_objects.bindings.find((b) => b.name === "SANDBOX");
  assert.equal(binding?.class_name, "Sandbox");
  assert.ok(prod.migrations[0].new_sqlite_classes.includes("Sandbox"));
});

test("declares one Session Durable Object per session, the ledger, the workflow, D1, R2 and Secrets Store bindings", () => {
  const names = prod.durable_objects.bindings.map((b) => b.name);
  assert.deepEqual(names.sort(), ["LEDGER", "SANDBOX", "SESSION"]);
  assert.equal(prod.workflows[0].binding, "TASK_WORKFLOW");
  assert.equal(prod.d1_databases[0].binding, "DB");
  assert.equal(prod.r2_buckets[0].binding, "ARTIFACTS");
  assert.deepEqual(prod.secrets_store_secrets.map((s) => s.binding).sort(), ["ACCESS_SECRET", "MODEL_KEY"]);
});

test("never publishes ungated URLs", () => {
  assert.equal(prod.workers_dev, false);
  assert.equal(prod.preview_urls, false);
});

test("contains no account id, no real database id and no secret value", () => {
  for (const rel of ["wrangler.jsonc", "wrangler.test.jsonc"]) {
    const text = read(rel);
    assert.ok(!/\b[0-9a-f]{32}\b/i.test(text), `${rel} has a 32-hex id`);
    assert.ok(!/account_id/.test(text), `${rel} has an account_id`);
    assert.ok(!/sk-[A-Za-z0-9_-]{8,}/.test(text), `${rel} has a key-shaped value`);
  }
  assert.equal(prod.d1_databases[0].database_id, "00000000-0000-0000-0000-000000000000");
  assert.equal(prod.vars.AI_GATEWAY_ACCOUNT, "");
});

test("the test configuration differs from production only by entry, containers and a short approval timeout", () => {
  assert.equal(testCfg.main, "test/entry.ts");
  assert.equal(testCfg.containers, undefined);
  assert.equal(testCfg.vars.APPROVAL_TIMEOUT, "2 seconds");
  assert.equal(prod.vars.APPROVAL_TIMEOUT, "24 hours");
  assert.deepEqual(
    testCfg.secrets_store_secrets.map((s) => s.binding),
    prod.secrets_store_secrets.map((s) => s.binding),
  );
});

test("the sandbox image is pinned to the SDK version and holds no credential", () => {
  const docker = read("sandbox/Dockerfile");
  const pkg = JSON.parse(read("package.json"));
  assert.ok(docker.includes(`cloudflare/sandbox:${pkg.dependencies["@cloudflare/sandbox"]}`));
  assert.ok(!/\b(ENV|ARG)\b[^\n]*(KEY|TOKEN|SECRET|PASSWORD)/i.test(docker));
});

test("the migration creates every table the code reads", () => {
  const sql = read("migrations/0001_init.sql");
  for (const t of ["audit", "spend", "task_cap_extra", "tasks", "memory_blocks", "memory_history", "memory_history_fts"]) {
    assert.ok(new RegExp(`CREATE (VIRTUAL )?TABLE IF NOT EXISTS ${t}\\b`).test(sql), t);
  }
});
