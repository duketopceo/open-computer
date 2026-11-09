import { test } from "node:test";
import assert from "node:assert/strict";
import { checkBudget, chunkName } from "./budget.mjs";

const budget = { main: 1000, chunks: { motionFeatures: 500 } };

test("chunk names drop the content hash", () => {
  assert.equal(chunkName("index-CaLb75yj.js"), "index");
  assert.equal(chunkName("motionFeatures-BOvnro0E.js"), "motionFeatures");
});

test("passes when every chunk is under its budget", () => {
  assert.deepEqual(checkBudget([{ file: "index-AAAAAAAA.js", gzip: 999, entry: true }, { file: "motionFeatures-BBBBBBBB.js", gzip: 10, entry: false }], budget), []);
});

test("fails an entry chunk over the main budget", () => {
  const p = checkBudget([{ file: "index-AAAAAAAA.js", gzip: 1001, entry: true }], budget);
  assert.equal(p.length, 1);
  assert.match(p[0], /over its 1000 B budget/);
});

test("fails a lazy chunk with no budget entry", () => {
  const p = checkBudget([{ file: "index-AAAAAAAA.js", gzip: 1, entry: true }, { file: "surprise-CCCCCCCC.js", gzip: 1, entry: false }], budget);
  assert.match(p[0], /no budget for chunk "surprise"/);
});

test("fails when no entry chunk was found", () => {
  assert.match(checkBudget([], budget)[0], /no entry chunk/);
});
