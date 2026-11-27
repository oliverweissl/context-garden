import test from "node:test";
import assert from "node:assert/strict";
import { CATALOG, addItem, removeItem, findItem, itemWords, joinWords, splitFlags } from "./launch-catalog.js";
const model = CATALOG.claude.flags.find((f) => f.name === "--model");
const chrome = CATALOG.claude.flags.find((f) => f.name === "--chrome");
const telemetry = CATALOG.claude.env.find((e) => e.name === "DISABLE_TELEMETRY");

test("a flag without a value is a single word", () => {
  assert.deepEqual(addItem([], chrome, "flag"), ["--chrome"]);
  assert.deepEqual(removeItem(["--chrome", "--ide"], chrome, "flag"), ["--ide"]);
});

test("a flag with a value takes it along and replaces an earlier one", () => {
  const on = addItem(["--chrome"], model, "flag", "sonnet");
  assert.deepEqual(on, ["--chrome", "--model", "sonnet"]);
  assert.deepEqual(addItem(on, model, "flag", "opus"), ["--chrome", "--model", "opus"]);
  assert.deepEqual(removeItem(on, model, "flag"), ["--chrome"]);
});

test("the --flag=value form is found and removed whole", () => {
  assert.equal(findItem(["--model=opus"], model, "flag"), 0);
  assert.deepEqual(removeItem(["--model=opus", "--ide"], model, "flag"), ["--ide"]);
});

test("a value with spaces survives joining and splitting", () => {
  const words = itemWords(model, "flag", "my model");
  assert.deepEqual(words, ["--model", "my model"]);
  assert.deepEqual(splitFlags(joinWords(words)), words);
  assert.deepEqual(splitFlags(joinWords(["FOO=a b", "--x"])), ["FOO=a b", "--x"]);
});

test("environment entries are NAME=value words", () => {
  assert.deepEqual(addItem([], telemetry, "env"), ["DISABLE_TELEMETRY=1"]);
  assert.deepEqual(addItem(["DISABLE_TELEMETRY=0"], telemetry, "env", "1"), ["DISABLE_TELEMETRY=1"]);
  assert.deepEqual(removeItem(["A=1", "DISABLE_TELEMETRY=1"], telemetry, "env"), ["A=1"]);
});

test("every entry is documented", () => {
  for (const agent of Object.values(CATALOG)) {
    for (const it of [...agent.flags, ...agent.env]) assert.ok(it.name && it.doc, it.name);
  }
});
