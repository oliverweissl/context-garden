// node --test (npm test): the checklist detector and the text sent to the agent.
import { test } from "node:test";
import assert from "node:assert/strict";
import { detectChecklist, carryOver, formatResults, formatReference } from "./checklist.js";

const claude = (s) => s.split("\n");

test("numbered steps for the user, as Claude Code renders them", () => {
  const found = detectChecklist(claude(`
⏺ The migration is written. Before I continue, please do these steps yourself:

  1. Open the **admin console** and create the bucket
  2. Copy the access key into .env as
     S3_KEY=...
  3. Restart the dev server

  Tell me when you're done.`));
  assert.equal(found.title, "The migration is written. Before I continue, please do these steps yourself");
  assert.deepEqual(found.items.map((i) => i.text), [
    "Open the admin console and create the bucket",
    "Copy the access key into .env as S3_KEY=...",
    "Restart the dev server",
  ]);
  assert.match(found.hash, /^[0-9a-f]{8}$/);
});

test("same list, same hash; different list, different hash", () => {
  const a = detectChecklist(["Next steps for you:", "1) a", "2) b", "3) c"]);
  const b = detectChecklist(["", "Next steps for you:", "1) a", "2) b", "3) c", ""]);
  const c = detectChecklist(["Next steps for you:", "1) a", "2) b", "3) d"]);
  assert.equal(a.hash, b.hash);
  assert.notEqual(a.hash, c.hash);
});

test("Step N:, markdown checkboxes and ☐ glyphs", () => {
  assert.equal(detectChecklist(["Please follow these:", "Step 1: x", "Step 2: y", "Step 3: z"]).items.length, 3);
  const boxes = detectChecklist(["Deploy notes", "- [ ] tag", "- [x] build", "- [ ] publish"]);
  assert.deepEqual(boxes.items.map((i) => i.checked), [false, true, false]);
  assert.equal(boxes.title, "Checklist");
  assert.equal(detectChecklist(["Manual checklist:", "☐ one", "☐ two", "☐ three"]).items.length, 3);
});

test("ignores what is not a task list for the user", () => {
  // Fewer than three items.
  assert.equal(detectChecklist(["Please do:", "1. a", "2. b"]), null);
  // The agent's own summary: nothing addresses the user.
  assert.equal(detectChecklist(["⏺ Changes made:", "  1. Fixed the parser", "  2. Added tests", "  3. Updated docs"]), null);
  // Claude Code's own todo panel.
  assert.equal(detectChecklist(["⏺ Update Todos", "  ⎿  ☐ Read the code", "     ☐ Write the fix", "     ☐ Run tests"]), null);
  // A permission menu.
  assert.equal(detectChecklist([
    " Do you want to proceed?", " ❯ 1. Yes", "   2. Yes, and don't ask again for this command", "   3. No, and tell Claude what to do differently",
  ]), null);
  // What "Send to agent" typed back, echoed in Claude Code's history.
  const sent = formatResults({ items: [{ text: "a" }, { text: "b" }, { text: "c" }], checks: [true, false, true], comments: ["", "", ""] });
  assert.equal(detectChecklist(sent.split("\n").map((l, i) => (i ? "  " : "> ") + l)), null);
  assert.equal(detectChecklist(formatReference({ items: [{ text: "a" }, { text: "b" }, { text: "c" }], checks: [], comments: [] }).split("\n")), null);
  // Numbering that does not run 1, 2, 3.
  assert.equal(detectChecklist(["Please do:", "1. a", "3. b", "4. c"]), null);
});

test("the last list wins; a gap of two blank lines ends one", () => {
  const found = detectChecklist([
    "Please do:", "1. a", "2. b", "3. c", "", "Then you also need to:", "1. d", "2. e", "3. f",
  ]);
  assert.deepEqual(found.items.map((i) => i.text), ["d", "e", "f"]);
  assert.equal(detectChecklist(["Please do:", "1. a", "", "", "2. b", "3. c"]), null);
});

test("carryOver keeps checks and comments by item text", () => {
  const prev = { items: [{ text: "a" }, { text: "b" }, { text: "c" }], checks: [true, false, true], comments: ["ok", "", ""] };
  const next = carryOver(prev, { title: "t", hash: "h", items: [{ text: "a" }, { text: "b" }, { text: "c" }, { text: "d", checked: true }] });
  assert.deepEqual(next.checks, [true, false, true, true]);
  assert.deepEqual(next.comments, ["ok", "", "", ""]);
});

test("text for the agent", () => {
  const state = {
    items: [{ text: "Create the bucket" }, { text: "Copy the key" }, { text: "Restart" }],
    checks: [true, false, true], comments: ["named it prod-1", "", "took a while\nbut fine"],
  };
  assert.equal(formatResults(state), [
    "Checklist results (2/3 done):",
    "1. [x] Create the bucket — comment: named it prod-1",
    "2. [ ] Copy the key — not done",
    "3. [x] Restart — comment: took a while / but fine",
  ].join("\n"));
  assert.equal(formatReference(state), [
    "Re checklist (2/3 done):",
    "1. [x] Create the bucket — comment: named it prod-1",
    "2. [ ] Copy the key",
    "3. [x] Restart — comment: took a while / but fine",
    "Question: ",
  ].join("\n"));
});
