// Documented launch flags and environment variables per agent, for the "Flags" picker in the
// terminal toolbar. Flags come from `claude --help` / `codex --help`; the variables are ones the
// Claude binary reads. `value` is a placeholder: the entry takes a value the user types.
export const CATALOG = {
  claude: {
    flags: [
      { name: "--model", value: "opus", doc: "Model for the session: an alias (opus, sonnet) or a full name." },
      { name: "--effort", value: "high", doc: "Effort level: low, medium, high, xhigh, max." },
      { name: "--permission-mode", value: "plan", doc: "acceptEdits, auto, bypassPermissions, manual, dontAsk or plan." },
      { name: "--chrome", doc: "Turn on Claude in Chrome." },
      { name: "--no-chrome", doc: "Turn off Claude in Chrome." },
      { name: "--ide", doc: "Connect to your IDE on startup if exactly one is available." },
      { name: "--worktree", doc: "Create a git worktree for the session." },
      { name: "--add-dir", value: "../other", doc: "Give Claude access to more directories." },
      { name: "--allowed-tools", value: "Edit", doc: "Tools to allow without asking, e.g. \"Bash(git *) Edit\"." },
      { name: "--disallowed-tools", value: "Bash", doc: "Tools to deny." },
      { name: "--tools", value: "default", doc: "Built-in tools to make available: \"\" for none, default, or names." },
      { name: "--disable-slash-commands", doc: "Disable all skills and commands." },
      { name: "--strict-mcp-config", doc: "Only use MCP servers from --mcp-config." },
      { name: "--mcp-config", value: "mcp.json", doc: "Load MCP servers from a JSON file." },
      { name: "--settings", value: "settings.json", doc: "Load more settings from a file or JSON string." },
      { name: "--setting-sources", value: "user,project", doc: "Which setting sources to load: user, project, local." },
      { name: "--append-system-prompt", value: "text", doc: "Add text to the end of the system prompt." },
      { name: "--safe-mode", doc: "Start with all customizations (CLAUDE.md, skills, plugins, hooks, MCP) off." },
      { name: "--bare", doc: "Minimal mode: no hooks, plugin sync, auto-memory or CLAUDE.md discovery." },
      { name: "--restricted", doc: "Remove tools that run commands, ignore user/project settings." },
      { name: "--verbose", doc: "Override the verbose setting." },
      { name: "--debug", doc: "Enable debug mode." },
      { name: "--dangerously-skip-permissions", doc: "Bypass all permission checks. Only for sandboxes without internet.", danger: true },
    ],
    env: [
      { name: "DISABLE_TELEMETRY", value: "1", doc: "Opt out of usage telemetry." },
      { name: "DISABLE_ERROR_REPORTING", value: "1", doc: "Opt out of error reporting." },
      { name: "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", value: "1", doc: "No telemetry, error reporting, auto-updates or other non-essential requests." },
      { name: "DISABLE_AUTOUPDATER", value: "1", doc: "Do not update Claude Code by itself." },
      { name: "MAX_THINKING_TOKENS", value: "8000", doc: "Token budget for extended thinking." },
    ],
  },
  codex: {
    flags: [
      { name: "--model", value: "gpt-5", doc: "Model the agent should use." },
      { name: "--search", doc: "Enable live web search." },
      { name: "--sandbox", value: "workspace-write", doc: "Sandbox for shell commands: read-only, workspace-write, danger-full-access." },
      { name: "--ask-for-approval", value: "on-request", doc: "When to ask before running a command: on-request or never." },
      { name: "--approve-for-me", doc: "Route approval requests through automatic review in the workspace-write sandbox." },
      { name: "--add-dir", value: "../other", doc: "More directories that should be writable." },
      { name: "--oss", doc: "Use an open-source (local) provider." },
      { name: "--no-alt-screen", doc: "Inline mode: keep terminal scrollback." },
      { name: "--strict-config", doc: "Fail on unknown fields in config.toml." },
      { name: "--dangerously-bypass-approvals-and-sandbox", doc: "No prompts and no sandbox. Only for externally sandboxed environments.", danger: true },
      { name: "--dangerously-bypass-hook-trust", doc: "Run enabled hooks without persisted hook trust.", danger: true },
    ],
    env: [],
  },
};

/** Split a flags line like a shell would: spaces separate, quotes group (also mid-word, as in FOO="a b"). */
export function splitFlags(line) {
  const out = [];
  for (const [word] of line.matchAll(/(?:"[^"]*"|'[^']*'|[^\s"'])+/g)) {
    out.push(word.replace(/"([^"]*)"|'([^']*)'/g, (_, d, q) => d ?? q));
  }
  return out;
}

/** Join words into a line splitFlags reads back to the same words. */
export function joinWords(words) {
  return words.map((w) => (/[\s"']/.test(w) ? (w.includes('"') ? `'${w}'` : `"${w}"`) : w)).join(" ");
}

/** The words a catalog item contributes: ["--model", "opus"], ["--chrome"], ["NAME=1"]. */
export function itemWords(item, kind, value) {
  if (kind === "env") return [`${item.name}=${value ?? item.value}`];
  return item.value !== undefined && value !== undefined && value !== "" ? [item.name, value] : [item.name];
}

/** Index of the item in a split line, or -1. Flags may be written `--flag value` or `--flag=value`. */
export function findItem(words, item, kind) {
  return words.findIndex((w) => (kind === "env" ? w.startsWith(`${item.name}=`) : w === item.name || w.startsWith(`${item.name}=`)));
}

/** Words with the item switched off (with its value, when it takes one). */
export function removeItem(words, item, kind) {
  const i = findItem(words, item, kind);
  if (i < 0) return words;
  const own = kind === "flag" && words[i] === item.name && item.value !== undefined && words[i + 1] && !words[i + 1].startsWith("-");
  return [...words.slice(0, i), ...words.slice(i + (own ? 2 : 1))];
}

/** Words with the item switched on, replacing an earlier setting of it. */
export function addItem(words, item, kind, value) {
  return [...removeItem(words, item, kind), ...itemWords(item, kind, value)];
}
