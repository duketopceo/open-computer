import assert from "node:assert/strict";
import test from "node:test";
import { checkBundle, MARKER } from "./check-demo-bundle.mjs";

const html = '<html><head><script src="/static/theme-init.js"></script></head><body><script type="module" src="/assets/index-abc.js"></script></body></html>';
const present = new Set(["static/theme-init.js", "assets/index-abc.js"]);
const exists = (r) => present.has(r);
const clean = [{ name: "index-abc.js", text: "console.log(1)" }];
const demo = [{ name: "demo-xyz.js", text: `x="${MARKER}"` }];

test("a clean prod bundle passes", () => assert.deepEqual(checkBundle({ html, files: clean, exists }, "prod"), []));
test("prod with the demo marker fails", () => assert.equal(checkBundle({ html, files: demo, exists }, "prod").length, 1));
test("demo without the marker fails", () => assert.equal(checkBundle({ html, files: clean, exists }, "demo").length, 1));
test("demo with the marker passes", () => assert.deepEqual(checkBundle({ html, files: demo, exists }, "demo"), []));
test("an inline script fails", () => {
  const bad = html.replace("</body>", "<script>alert(1)</script></body>");
  assert.ok(checkBundle({ html: bad, files: clean, exists }, "prod").some((p) => p.includes("inline")));
});
test("a referenced file missing from the dist fails", () => {
  const p = checkBundle({ html, files: clean, exists: (r) => r !== "static/theme-init.js" }, "demo");
  assert.ok(p.some((x) => x.includes("/static/theme-init.js")));
});
test("prod does not require backend-served files such as /static/theme-init.js", () => {
  assert.deepEqual(checkBundle({ html, files: clean, exists: (r) => r !== "static/theme-init.js" }, "prod"), []);
});
