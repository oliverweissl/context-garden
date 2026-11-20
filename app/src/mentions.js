// `@"Worker name" do this` / `@all do this` typed in a manager terminal goes straight to
// those workers instead of through the manager's own judgement. Anything that does not
// name a linked worker (for example Claude Code's own @file references) is left alone.

const MENTION = /^@(?:"([^"]+)"|(\S+))\s+([\s\S]+)$/;

/**
 * Parse `line` against the manager's workers ({ key, title }[]).
 * Returns { targets, body, all } or null when it is not a mention of one of them.
 */
export function parseMention(line, workers) {
  const m = MENTION.exec(line.trim());
  if (!m) return null;
  const name = (m[1] ?? m[2]).trim().toLowerCase();
  const body = m[3].trim();
  if (!body || !workers.length) return null;
  if (name === "all" && m[1] === undefined) return { targets: workers, body, all: true };
  const exact = workers.filter((w) => w.title.toLowerCase() === name);
  const hits = exact.length ? exact : workers.filter((w) => w.title.toLowerCase().startsWith(name));
  return hits.length === 1 ? { targets: hits, body, all: false } : null;
}

/** What a worker is told when the user addressed it directly through its manager. */
export const frameForWorker = (managerTitle, body) =>
  `[Task from your manager "${managerTitle}"] ${body}\n(When done or blocked, tell your manager with the report_to_manager tool.)`;

/** What the manager is told, so it knows what its workers were asked and does not repeat it. */
export const noteForManager = (targets, all, body) =>
  `[FYI: the user messaged ${all ? "all your workers" : `your worker "${targets[0].title}"`} directly with: ${body}\nNo action needed; their report will arrive as usual.]`;

/**
 * Tracks what the user has typed on the current input line. Returns null once it cannot
 * be trusted (cursor keys, history, paste, tab) until the next Enter.
 */
export function makeDraft() {
  let text = "";
  let ok = true;
  return {
    feed(d) {
      if (d === "\r") {
        const out = ok ? text : null;
        text = "";
        ok = true;
        return { enter: true, line: out };
      }
      if (d === "\x7f") text = text.slice(0, -1);
      else if (d === "\x15" || d === "\x03") { text = ""; ok = true; }
      else if (/^[ -~ -￿]$/.test(d)) text += d;
      else ok = false; // escape sequences, tabs, multi-char pastes
      return { enter: false, line: ok ? text : null };
    },
  };
}
