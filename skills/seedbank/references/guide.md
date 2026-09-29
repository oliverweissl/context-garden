# seedbank: operational details

## Automatic observation (Claude Code hook)

The context-garden plugin's `hooks/hooks.json` runs `bin/seedbank hook`
asynchronously after every `Read`, `Grep` and `Glob` (silent, always exits
0, only inside a git repo). If you copied just this skill, add to
`.claude/settings.json` (or `~/.claude/settings.json`):
```json
{"hooks": {"PostToolUse": [{"matcher": "Read|Grep|Glob",
  "hooks": [{"type": "command", "command": "<this-skill-dir>/bin/seedbank hook",
  "async": true, "timeout": 5}]}]}}
```

## Observing reads and searches by hand

Without the hook:
```
<this-skill-dir>/bin/seedbank observe read <path> --task "<why you read it>" [--scope <topic>]
<this-skill-dir>/bin/seedbank observe search "<pattern>" --task "<why>" [--scope <topic>]
```
Example mistake/fact logs:
```
<this-skill-dir>/bin/seedbank observe mistake "Do not edit src/generated/**; regenerate with tools/codegen.py." \
  --scope invariants --source src/generated/some_file.cpp
<this-skill-dir>/bin/seedbank observe fact "Never weaken convergence tolerances to make tests pass; tolerance is part of the numerical accuracy contract." \
  --scope invariants
```

## Tiers

- **Hot**: inlined in `AGENTS.md`, budget 500 tokens by default.
- **Warm**: `.seedbank/warm/<scope>.md`, only pointed to from `AGENTS.md`;
  open one only when the task is about that topic.
- **Cold**: not stored; the repository itself.

## Import

`import` seeds bullets as lower-confidence candidates; nothing is promoted
until you review them.

## In the target repository

Commit `AGENTS.md` and `.seedbank/warm/*.md`. A new store writes
`.seedbank/.gitignore` ignoring local profiler state (everything except
`warm/`); commit it too, and edit it only if the team decides to share
observation history. An existing `.seedbank/.gitignore` is never
overwritten.

If Claude Code works in this repository, also commit a one-line
`CLAUDE.md` containing `@AGENTS.md`. Claude Code reads `CLAUDE.md`, never
`AGENTS.md`; the import avoids a second copy, so `compile` only touches
`AGENTS.md`. A symlink also works but fails on Windows without
admin/Developer Mode.
