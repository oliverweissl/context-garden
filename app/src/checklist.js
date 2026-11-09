/**
 * Checklists an agent hands to the user ("please do these steps"), found in a
 * terminal's rendered lines, and the text sent back about them. Pure: no DOM,
 * no xterm, so it runs under `node --test` (src/checklist.test.js).
 */

/** Item markers; `checked` is true when the marker already says done. */
const ITEMS = [
  ["num", /^(\d{1,2})[.)]\s+(\S.*)$/],
  ["step", /^step\s+(\d{1,2})\s*[:.)\-–—]\s*(\S.*)$/i],
  ["box", /^(?:[-*+•]\s+)?\[([ xX])\]\s+(\S.*)$/],
  ["glyph", /^([☐□☑☒✓✔])\s*(\S.*)$/u],
];
const DONE = /^[xX☑☒✓✔]$/u;

/** A line before the list that speaks to the user. */
const FOR_USER = /\b(?:you|your|yourself|please|manual(?:ly)?|next steps?|action items?|checklist|steps? (?:to|for)|following steps|on your (?:end|side))\b/i;
/**
 * Not a task list: the agent's own todo panel, a question with a menu below,
 * or the results this app typed back (echoed in the prompt or the history).
 */
const NOT_FOR_USER = /^(?:update )?todos?\b|\?\s*$|^(?:>\s*)?(?:checklist results|re checklist)\b/i;
/** TUI decoration in front of text: Claude Code's ⏺ and ⎿, box borders. */
const LEAD = /^[\s⏺●⎿│┃]*/u;
/** Selection cursor of a menu (permission prompts, pickers). */
const MENU = /^[\s│┃]*[❯›]\s/u;

const MIN_ITEMS = 3;
const MAX_ITEMS = 40;

function parseLine(raw) {
  const line = raw.replace(/\s+$/, "");
  const lead = line.match(LEAD)[0];
  const text = line.slice(lead.length);
  if (!text) return { blank: true };
  const menu = MENU.test(line);
  for (const [kind, re] of ITEMS) {
    const m = text.match(re);
    if (!m) continue;
    const n = kind === "num" || kind === "step" ? Number(m[1]) : null;
    return { indent: lead.length, kind, n, checked: DONE.test(m[1]), text: m[2], body: text, menu };
  }
  return { indent: lead.length, text, body: text, menu };
}

const tidy = (s) => s.replace(/\*\*|__/g, "").replace(/\s+/g, " ").trim();

/** FNV-1a over the item texts: the same list gives the same id. */
export function hashItems(items) {
  let x = 0x811c9dc5;
  for (const ch of items.map((i) => i.text).join("\n")) {
    x ^= ch.codePointAt(0);
    x = Math.imul(x, 0x01000193) >>> 0;
  }
  return x.toString(16).padStart(8, "0");
}

/**
 * The last checklist addressed to the user in `lines` (rendered terminal
 * rows, soft wraps already joined), or null. A checklist is at least 3
 * consecutive items of one kind ("1." / "1)" numbered from 1, "Step 1:",
 * "- [ ]", "☐"), lines indented deeper than an item continue it, one blank
 * line between items is fine. Numbered, step and glyph lists also need the
 * line just above to speak to the user ("please", "you", "next steps", …);
 * markdown checkboxes count on their own. Menus (❯ 1. Yes) never count.
 * Returns { title, items: [{ text, checked }], hash }.
 */
export function detectChecklist(lines) {
  const rows = lines.map(parseLine);
  const runs = [];
  let run = null, blanks = 0, lastText = -1;
  const end = () => {
    if (run && run.items.length >= MIN_ITEMS) runs.push(run);
    run = null;
  };
  rows.forEach((r, i) => {
    if (r.blank) {
      if (++blanks > 1) end();
      return;
    }
    blanks = 0;
    const prev = lastText;
    lastText = i;
    if (run && r.indent > run.indent + 1) {
      // Wrapped or nested text (sub-bullets, a command) belongs to the item above.
      run.items[run.items.length - 1].text += " " + r.body;
      run.menu ||= r.menu;
      return;
    }
    if (r.kind) {
      const next = run && run.kind === r.kind && Math.abs(r.indent - run.indent) <= 1
        && (r.n == null || r.n === run.items.length + 1);
      if (next && run.items.length < MAX_ITEMS) {
        run.items.push({ text: r.text, checked: r.checked });
        run.menu ||= r.menu;
        return;
      }
      end();
      if (r.n == null || r.n === 1) {
        run = { kind: r.kind, indent: r.indent, intro: prev, items: [{ text: r.text, checked: r.checked }], menu: r.menu };
      }
      return;
    }
    end();
  });
  end();

  for (const c of runs.reverse()) {
    if (c.menu) continue;
    const intro = c.intro >= 0 ? tidy(rows[c.intro].text) : "";
    if (intro && NOT_FOR_USER.test(intro)) continue;
    const introOk = !!intro && FOR_USER.test(intro);
    if (!introOk && c.kind !== "box") continue;
    const items = c.items.map((it) => ({ text: tidy(it.text), checked: it.checked })).filter((it) => it.text);
    if (items.length < MIN_ITEMS) continue;
    const title = (introOk ? intro.replace(/[:：]\s*$/, "") : "") || "Checklist";
    return { title, items, hash: hashItems(items) };
  }
  return null;
}

/**
 * Checks and comments of an older detection carried over to a newer one with
 * the same item texts (a list seen half-printed, then whole).
 */
export function carryOver(prev, next) {
  const byText = new Map((prev?.items || []).map((it, i) => [it.text, {
    checked: !!prev.checks?.[i], comment: prev.comments?.[i] || "",
  }]));
  return {
    ...next,
    checks: next.items.map((it) => byText.get(it.text)?.checked ?? it.checked),
    comments: next.items.map((it) => byText.get(it.text)?.comment ?? ""),
  };
}

const oneLine = (s) => String(s || "").trim().replace(/\s*\n\s*/g, " / ");
const doneCount = (state) => state.items.filter((_, i) => state.checks[i]).length;

function lines(state, notDone) {
  return state.items.map((it, i) => {
    const done = !!state.checks[i];
    const comment = oneLine(state.comments[i]);
    let s = `${i + 1}. [${done ? "x" : " "}] ${it.text}`;
    if (!done && notDone) s += " — not done";
    if (comment) s += ` — comment: ${comment}`;
    return s;
  });
}

/** What "Send to agent" types into the terminal (then Enter). */
export function formatResults(state) {
  return [`Checklist results (${doneCount(state)}/${state.items.length} done):`, ...lines(state, true)].join("\n");
}

/** What "Ask about this" types into the terminal, left open for the question. */
export function formatReference(state) {
  return [`Re checklist (${doneCount(state)}/${state.items.length} done):`, ...lines(state, false), "Question: "].join("\n");
}
