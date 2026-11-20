import { describe, it } from "node:test";
import assert from "node:assert/strict";
const expect = (v) => ({ toBe: (x) => assert.equal(v, x), toEqual: (x) => assert.deepEqual(v, x) });
import { isAsking, canDeliver, makeInbox, MAX_HOLD_MS } from "./inbox.js";

const idle = { lines: ["done.", "> "], sinceInput: 60_000, sinceOutput: 60_000, ageMs: 0 };

describe("canDeliver", () => {
  it("delivers to an idle agent", () => expect(canDeliver(idle)).toBe(true));
  it("holds while the user is typing or the agent streams", () => {
    expect(canDeliver({ ...idle, sinceInput: 500 })).toBe(false);
    expect(canDeliver({ ...idle, sinceOutput: 300 })).toBe(false);
  });
  it("holds while a question is on screen, however long it waited", () => {
    const q = { ...idle, lines: ["Which one?", "❯ 1. A", "  2. B", "Enter to select · ↑/↓ to navigate · Esc to cancel"], ageMs: 10 * MAX_HOLD_MS };
    expect(isAsking(q.lines)).toBe(true);
    expect(canDeliver(q)).toBe(false);
  });
  it("gives up waiting for output quiet (a spinner) after the max hold", () => {
    expect(canDeliver({ ...idle, sinceOutput: 10, ageMs: MAX_HOLD_MS })).toBe(true);
  });
});

describe("makeInbox", () => {
  function setup(initial) {
    let st = { ...idle, ...initial };
    let t = 0;
    const timers = [];
    const got = [];
    const inbox = makeInbox({
      state: () => st, deliver: (x) => got.push(x), now: () => t,
      setTimer: (fn) => timers.push(fn) && timers.length,
    });
    return { inbox, got, set: (s) => { st = { ...st, ...s }; }, tick: () => timers.shift()?.() };
  }
  it("sends at once when idle", () => {
    const { inbox, got } = setup();
    expect(inbox.push("report")).toBe("sent");
    expect(got).toEqual(["report"]);
  });
  it("queues during a question and delivers in order afterwards", () => {
    const { inbox, got, set, tick } = setup({ lines: ["Enter to select"] });
    expect(inbox.push("one")).toBe("queued");
    expect(inbox.push("two")).toBe("queued");
    tick();
    expect(got).toEqual([]);
    set({ lines: ["> "] });
    tick();
    expect(got).toEqual(["one"]);
    tick();
    expect(got).toEqual(["one", "two"]);
    expect(inbox.size()).toBe(0);
  });
});
