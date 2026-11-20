// Messages other agents send to a terminal (a worker's report, a manager's task) must not
// land while the agent is waiting for the user's answer or the user is typing: typed in
// then, they get read as that answer and are lost. They wait here until the terminal is idle.

/** Screen text that means "the agent is asking the user something right now". */
const ASKING = [
  /enter to select/i, // Claude Code's question picker
  /↑\/↓ to navigate|arrow keys to navigate/i,
  /esc to cancel/i,
  /do you want to (proceed|make this edit|create|run)/i, // permission prompts
  /\(y\/n\)|\[y\/n\]/i,
  /^\s*[❯›>]\s*1\.\s/m, // a numbered choice with the cursor on the first
  /press enter to confirm/i, // Codex approvals
];

/** True when the visible tail of the screen shows a question or choice waiting for the user. */
export const isAsking = (lines) => ASKING.some((re) => re.test(lines.slice(-14).join("\n")));

export const TYPING_QUIET_MS = 4000; // the user touched the keys more recently: they may be mid-answer
export const OUTPUT_QUIET_MS = 1500; // the agent is still streaming
export const MAX_HOLD_MS = 120_000; // after this a message goes in even if we cannot tell, unless a question is on screen

/**
 * Whether a queued message can be typed in now. `ageMs` is how long it has waited;
 * a question on screen blocks it for as long as it is there.
 */
export function canDeliver({ lines, sinceInput, sinceOutput, ageMs }) {
  if (isAsking(lines)) return false;
  if (sinceInput < TYPING_QUIET_MS) return false;
  return sinceOutput >= OUTPUT_QUIET_MS || ageMs >= MAX_HOLD_MS;
}

/**
 * A per-terminal queue. `state()` reports { lines, sinceInput, sinceOutput }, `deliver(text)`
 * types one message in. `push` returns "sent" when it went straight in, else "queued".
 */
export function makeInbox({ state, deliver, now = Date.now, setTimer = setTimeout, onChange = () => {} }) {
  const queue = [];
  let timer = null;
  const ready = (m) => canDeliver({ ...state(), ageMs: now() - m.at });
  function pump() {
    timer = null;
    if (queue.length && ready(queue[0])) {
      deliver(queue.shift().text);
      onChange(queue.length);
      // The agent starts working on it: give it a moment before the next one.
      if (queue.length) timer = setTimer(pump, 2500);
      return;
    }
    if (queue.length) timer = setTimer(pump, 700);
  }
  return {
    push(text) {
      const empty = queue.length === 0;
      queue.push({ text, at: now() });
      if (empty && ready(queue[0])) {
        pump();
        return "sent";
      }
      onChange(queue.length);
      if (!timer) timer = setTimer(pump, 700);
      return "queued";
    },
    size: () => queue.length,
  };
}
