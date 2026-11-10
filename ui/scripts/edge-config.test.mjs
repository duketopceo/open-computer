// The demo's deploy config can only ever be static and ungated-URL-free; the normal build cannot be made a demo.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const stripComments = (s) => s.replace(/^\s*\/\/.*$/gm, "");

test("wrangler.jsonc is static assets only, with no ungated URL", () => {
  const c = JSON.parse(stripComments(fs.readFileSync(path.join(root, "edge/wrangler.jsonc"), "utf8")));
  assert.equal(c.main, undefined, "no Worker script");
  for (const k of ["vars", "kv_namespaces", "d1_databases", "r2_buckets", "services", "durable_objects", "ai", "queues", "hyperdrive", "tunnel"]) {
    assert.equal(c[k], undefined, `no ${k}`);
  }
  assert.equal(c.workers_dev, false);
  assert.equal(c.preview_urls, false);
  assert.equal(c.assets.directory, "../ui/dist-demo");
  assert.equal(c.assets.not_found_handling, "single-page-application");
});

test("_headers carries a tight CSP", () => {
  const h = fs.readFileSync(path.join(root, "ui/public/_headers"), "utf8");
  const csp = h.match(/Content-Security-Policy:\s*(.+)/)?.[1] ?? "";
  assert.match(csp, /connect-src 'self'/);
  assert.match(csp, /script-src 'self'(;|$)/);
  assert.doesNotMatch(csp, /unsafe-eval|unsafe-inline|\*/);
  assert.match(h, /X-Content-Type-Options: nosniff/);
});

test("a normal build refuses to run with VITE_DEMO set, whatever its value", () => {
  for (const v of ["1", "0", "false"]) {
    const r = spawnSync("pnpm", ["exec", "vite", "build", "--outDir", "/tmp/oc-never-built"], {
      cwd: path.join(root, "ui"),
      env: { ...process.env, VITE_DEMO: v },
      encoding: "utf8",
    });
    assert.notEqual(r.status, 0, `VITE_DEMO=${v} must fail the normal build`);
    assert.match(r.stderr + r.stdout, /VITE_DEMO is set/);
  }
});
