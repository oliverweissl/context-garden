# seedbank: operational details

Moved out of `SKILL.md` to keep the on-trigger body small.

## Observing reads and searches by hand

Without the hook:
```
<this-skill-dir>/bin/seedbank observe read <path> --task "<why you read it>" [--scope <topic>]
<this-skill-dir>/bin/seedbank observe search "<pattern>" --task "<why>" [--scope <topic>]
```
`mistake` costs more (it represents wasted work) and ranks higher for
promotion than a plain discovery. `bin/seedbank status` shows the store.

## Scoring notes

Value counts *distinct sessions* that rediscovered a key and decays with a
30-day half-life. A correctness/safety invariant is always worth
promoting regardless of its ranked value.

## Compile

`compile` also re-runs invalidation, so a stale fact (its source file
changed since promotion) is automatically excluded rather than silently
served as if still true -- except `--critical` facts, which stay published
and are marked "(source changed — verify)". Hand-written text outside the
markers is kept. On a fresh clone (empty store) compile refuses to wipe an
existing block: run `import AGENTS.md` and re-promote first.

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
`AGENTS.md`; the import line loads the same facts without a second copy.
A symlink works too but fails on Windows without admin/Developer Mode.

## Guarantees

- Nothing is promoted just because it was seen once -- promotion is always
  a deliberate `seedbank promote` call.
- Every fact links back to the source file(s) it came from (when it has
  any); if those files change, the fact is marked stale and excluded from
  `compile` output (or, if `--critical`, kept with a "verify" mark) until
  revalidated (`seedbank invalidate --confirm <id>`) or demoted.
- All scoring/bookkeeping is deterministic (no model calls).

## Rules, verbatim

- It never guesses what's worth remembering from a single encounter -- it
  tracks how often something gets rediscovered (or how costly a mistake
  was) and only promotes a fact to persistent context when you (the agent)
  deliberately decide it's worth it.
- `seedbank` is not on `PATH`: always run it as `<this-skill-dir>/bin/seedbank`
  (bare `seedbank <cmd>` is shorthand for that).
- Before exploring, check what's already cached: `cat AGENTS.md` (hot facts,
  always relevant) and `<this-skill-dir>/bin/seedbank status`.
- Example mistake/fact logs:
  `<this-skill-dir>/bin/seedbank observe mistake "Do not edit src/generated/**; regenerate with tools/codegen.py." --scope invariants --source src/generated/some_file.cpp`
  and
  `<this-skill-dir>/bin/seedbank observe fact "Never weaken convergence tolerances to make tests pass; tolerance is part of the numerical accuracy contract." --scope invariants`.
- A correctness/safety invariant is always worth promoting regardless of its
  ranked value -- use `--critical` when you promote it so it's protected
  from budget-based eviction.
- Claude Code reads `CLAUDE.md`, never `AGENTS.md` -- no automatic fallback,
  confirmed in Anthropic's own docs.
- The `@AGENTS.md` import line makes Claude Code load the same facts every
  other AGENTS.md-reading agent already gets, without a second copy to keep
  in sync (`seedbank compile` only ever needs to touch `AGENTS.md`; this stub
  never changes again).
