# seedbank: operational details

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
`mistake` costs more (it represents wasted work) and ranks higher for
promotion than a plain discovery. `bin/seedbank status` shows the store.

## Import

`import` seeds each bullet as a review-before-trusting candidate (lower
confidence) rather than promoting it outright.

## Automatic observation (Claude Code hook)

The context-garden plugin ships `hooks/hooks.json`, which runs
`bin/seedbank hook` asynchronously after every `Read`, `Grep` and `Glob`
(background, silent, always exits 0, only inside a git repo). If you copied
just this skill, add to `.claude/settings.json` (or `~/.claude/settings.json`):
```json
{"hooks": {"PostToolUse": [{"matcher": "Read|Grep|Glob",
  "hooks": [{"type": "command", "command": "<this-skill-dir>/bin/seedbank hook",
  "async": true, "timeout": 5}]}]}}
```

## Tiers

- **Hot** = always-loaded (`AGENTS.md`). Kept small on purpose (default
  budget 500 tokens); promoting past budget auto-demotes the
  lowest-value non-`--critical` hot fact to warm.
- **Warm** = topic-scoped files under `.seedbank/warm/<scope>.md`,
  pointed to from `AGENTS.md` but not inlined -- open one only when the
  current task is actually about that topic.
- **Cold** = everything else; just the repository itself.

## In the target repository

Commit `AGENTS.md` (and any `.seedbank/warm/*.md`). A new store is created
with `.seedbank/.gitignore` ignoring the local profiler state
(`observations.jsonl`, `keystats.json`, `facts.json`, `config.json`,
`.lock`, `hook.log`, `*.tmp`) but not `warm/`; commit that `.gitignore`
too, and edit it only if the team decides to share observation history.
An existing `.seedbank/.gitignore` is never overwritten.

If Claude Code works in this repository, also commit a one-line
`CLAUDE.md` containing `@AGENTS.md`. Claude Code reads `CLAUDE.md`, never
`AGENTS.md` (no automatic fallback); the import line loads the same facts
without a second copy, so `seedbank compile` only ever touches `AGENTS.md`.
A symlink works too but fails on Windows without admin/Developer Mode.
