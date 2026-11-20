import { test } from "node:test";
import assert from "node:assert/strict";
import { parseMention, makeDraft, frameForWorker } from "./mentions.js";

const W = [{ key: "a", title: "Claude Code · Alice" }, { key: "b", title: "Codex" }, { key: "c", title: "Codex 2" }];

test("quoted names, prefixes, @all", () => {
  assert.deepEqual(parseMention('@"Claude Code · Alice" fix it', W).targets, [W[0]]);
  assert.equal(parseMention("@claude do x", W).targets[0].key, "a");
  assert.equal(parseMention("@all run tests", W).targets.length, 3);
  assert.equal(parseMention("@all run tests", W).body, "run tests");
});
test("exact beats prefix; ambiguous and unknown pass through", () => {
  assert.equal(parseMention("@codex hi", W).targets[0].key, "b");
  assert.equal(parseMention("@co hi", W), null);
  assert.equal(parseMention("@src/main.rs explain", W), null);
  assert.equal(parseMention("@all", W), null);
  assert.equal(parseMention("hello @all", W), null);
  assert.equal(parseMention("@all hi", []), null);
});
test("draft tracking", () => {
  const d = makeDraft();
  for (const c of '@all go') d.feed(c);
  d.feed("x"); d.feed("\x7f");
  assert.deepEqual(d.feed("\r"), { enter: true, line: "@all go" });
  d.feed("@"); d.feed("\x1b[A"); // history: cannot trust the line
  assert.deepEqual(d.feed("\r"), { enter: true, line: null });
  assert.deepEqual(d.feed("a"), { enter: false, line: "a" });
});
test("frame", () => assert.match(frameForWorker("M", "do"), /Task from your manager "M"\] do/));
