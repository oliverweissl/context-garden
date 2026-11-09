# mycelium protocol

## Gate (PreToolUse hook on `Agent`)

`bin/mycelium hook pre-agent` reads the hook JSON on stdin. A denied call
costs the parent a full resend of its brief, so the gate completes briefs
(`updatedInput`, decision `allow`) instead of refusing them:

1. **brief**: a missing `Task:` line is taken from the description; missing
   `Return:` and `Budget:` lines get defaults (the budget is the agent
   type's default, scaled by the level).
2. **known**: notes sharing at least 2 content words (or a path) with the
   task are added as a `Known:` block when the brief has none.
3. **route**: `general-purpose` (or no type) runs as a specialist when one
   is installed: an edit (implement/fix/add/...) as `landscaper`, a
   test/build run as `botanist`, anything else as `scout`. A
   `Why general:` line keeps `general-purpose`.
4. **repeat**: a `Task:` line whose content words overlap an agent already
   allowed in this session by 90% or more is denied; from
   `duplicate_threshold` (Jaccard, default 0.6) it passes with a one-line
   hint. A `Not a duplicate:` line skips this check.

Allowed calls are logged to `spawns.jsonl`. A hook error (e.g. a corrupt
`notes.json`) exits 1 with its traceback: Claude Code shows it as a
non-blocking error and the call runs.

## Budget (PreToolUse hook on every tool, subagents only)

`bin/mycelium hook budget` counts each subagent's tool calls (Claude Code
stamps `agent_id`/`agent_type` on them; main-thread calls carry neither and
leave the wrapper without starting Python). The limit is the first number
on the brief's `Budget:` line (read from the agent's own transcript), else
the type's default x the level, clamped to 5-400 calls. Three calls before
the limit the agent gets one wrap-up line; past it every call is denied
with "reply now with what you have, marked partial". Counters live in
`$TMPDIR/mycelium-budget/<session>/`, are dropped at SubagentStop and
pruned after a day.

Defaults (medium): scout 20, envoy 20, botanist 15, landscaper 40, Explore
25, general-purpose 40, other 50. Levels: low x0.5, medium x1, high x2,
extreme x4. The level comes from the plugin option `agent_budget`
(`CLAUDE_PLUGIN_OPTION_AGENT_BUDGET`), then `MYCELIUM_BUDGET`, then
`budget_level` in `.mycelium/config.json` (`bin/mycelium budget <level>`
sets it); `budgets` there overrides per-type defaults.

`bin/mycelium hook subagent-stop` (SubagentStop) reads the finished
agent's final reply (`last_assistant_message`, so background agents are
covered too) and records each line of its `Notes:` block:
```
Notes:
- <topic> | <finding> | <file> [<file>...]
```
`path:line` is reduced to the path; a line whose files don't exist is
skipped (it could never go stale). Same topic replaces the old note, as
with `note`. `hook post-agent` is a no-op kept for old settings.

## Agents

`scout` (read-only search; preloads pruner when it is installed) ships with
the plugin. A copied skill installs it with `bin/mycelium install-agents`.

## Modes

`.mycelium/config.json`: `{"gate": "enforce" | "warn" | "off",
"duplicate_threshold": 0.6, "budget_level": "medium", "budgets": {}}`.
`MYCELIUM_GATE` overrides `gate`. `warn` changes nothing and says, as
context, what `enforce` would complete or deny. `off` also turns off the
budget.

These tune the gate while the skill is on. The skill's own mode
(`bin/mycelium mode on|manual|off`) decides whether the hooks run at all:
in `manual` or `off`, Agent calls pass untouched.

## Hook setup without the plugin

Add to `.claude/settings.json`:
```json
{"hooks": {
  "PreToolUse":  [{"matcher": "Agent", "hooks": [{"type": "command",
    "command": "<this-skill-dir>/bin/mycelium hook pre-agent", "timeout": 5}]},
                  {"hooks": [{"type": "command",
    "command": "<this-skill-dir>/bin/mycelium hook budget", "timeout": 5}]}],
  "SubagentStop": [{"hooks": [{"type": "command",
    "command": "<this-skill-dir>/bin/mycelium hook subagent-stop", "timeout": 5}]}]
}}
```

For a hard boundary instead of guidance, deny the tool in settings
(`"permissions": {"deny": ["Agent"]}`).

## Notes

`notes.json`: `{"version": 1, "notes": [{"id", "topic", "kind",
"text", "paths": {path: sha256}, "created_at", "verified_at"}]}`. One note
per topic: `note` with an existing topic replaces it. A note is stale when
any path's current sha256 differs (or the file is gone); `brief` and the
gate still show it, marked STALE, so the agent re-checks it. Commit
`notes.json` to share findings; `spawns.jsonl` and `.lock` are ignored by
the generated `.mycelium/.gitignore`.

Write notes like `Auth token refresh: src/auth/refresh.ts, called by
middleware.ts; regression test tests/auth/refresh.test.ts`: where it
lives and how it connects, not the investigation story.

## Agents

`bin/mycelium install-agents` copies `agents/*.md` -- `scout` (read-only
search, haiku, `maxTurns: 20`), `landscaper` (one bounded edit, `maxTurns:
50`), `botanist` (runs checks, haiku, `maxTurns: 20`) -- to
`<repo>/.claude/agents/`; `maxTurns` is a backstop behind the budget hook,
never overwriting an existing file. Custom agent memory is stored per
agent name, so it can't be shared between agent types; shared notes go
through the parent's `Known:` line instead.
