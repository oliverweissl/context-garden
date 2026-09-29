# mycelium protocol

## Gate (PreToolUse hook on `Agent`)

`bin/mycelium hook pre-agent` reads the hook JSON on stdin and checks, in
order:

1. **brief**: the prompt has `Task:`, `Why delegate:`, `Return:` and
   `Budget:` lines (case-insensitive, optional `-`/`**`).
2. **repeat**: the `Task:` line's content words overlap an agent already
   allowed in this session by at least `duplicate_threshold` (Jaccard,
   default 0.6). A `Not a duplicate:` line skips this check.
3. **known**: notes sharing at least 2 content words (or a path) with the
   prompt exist and the prompt has no `Known:` line.

The first failing rule denies the call. The denial reason is what the model
reads, so it contains the fix (a template, the earlier agent's
description, or the matching notes). Allowed calls are logged to
`spawns.jsonl`. The hook fails open: any error allows the call.

`bin/mycelium hook post-agent` (PostToolUse on `Agent`) adds a one-line
reminder to record durable findings. A background agent's tool result is
only launch metadata, so notes are never captured automatically.

## Modes

`.mycelium/config.json`: `{"gate": "enforce" | "warn" | "off",
"duplicate_threshold": 0.6}`. `MYCELIUM_GATE` overrides `gate`. `warn`
allows the call and adds the denial text as context instead.

These tune the gate while the skill is on. The skill's own mode
(`bin/mycelium mode on|manual|off`) decides whether the hooks run at all:
in `manual` or `off`, Agent calls pass untouched.

## Hook setup without the plugin

Add to `.claude/settings.json`:
```json
{"hooks": {
  "PreToolUse":  [{"matcher": "Agent", "hooks": [{"type": "command",
    "command": "<this-skill-dir>/bin/mycelium hook pre-agent", "timeout": 5}]}],
  "PostToolUse": [{"matcher": "Agent", "hooks": [{"type": "command",
    "command": "<this-skill-dir>/bin/mycelium hook post-agent", "timeout": 5}]}]
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

`bin/mycelium install-agents` copies `agents/scout.md` (read-only: Read,
Grep, Glob; `model: haiku`; `maxTurns: 20`) to `<repo>/.claude/agents/`,
never overwriting an existing file. Custom agent memory is stored per
agent name, so it can't be shared between agent types; shared notes go
through the parent's `Known:` line instead.
