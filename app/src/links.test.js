import { test } from "node:test";
import assert from "node:assert/strict";
import { linkNotices } from "./links.js";

const titles = { a: "Boss", b: "Worker One", c: "Worker Two" };
const titleOf = (k) => titles[k] || k;

test("a new link tells the manager and the worker", () => {
  const out = linkNotices([], [{ manager: "a", worker: "b" }], titleOf);
  assert.equal(out.length, 2);
  assert.equal(out[0].key, "a");
  assert.match(out[0].text, /^\[Context Garden\] You now manage "Worker One"/);
  assert.match(out[0].text, /send_prompt/);
  assert.equal(out[1].key, "b");
  assert.match(out[1].text, /"Boss" now manages you/);
  assert.match(out[1].text, /report_to_manager/);
});

test("a removed link tells both ends; unchanged links say nothing", () => {
  const ab = { manager: "a", worker: "b" };
  const ac = { manager: "a", worker: "c" };
  const out = linkNotices([ab, ac], [{ ...ac }], titleOf);
  assert.deepEqual(out.map((n) => n.key), ["a", "b"]);
  assert.match(out[0].text, /no longer manage "Worker One"/);
  assert.match(out[1].text, /"Boss" no longer manages you/);
  assert.deepEqual(linkNotices([ab], [{ ...ab }], titleOf), []);
});
