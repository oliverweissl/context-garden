---
name: seedbank
description: Learn which repository facts (build commands, conventions, invariants, past mistakes) are worth persisting across sessions, and maintain the smallest useful AGENTS.md instead of hand-writing or re-discovering it every time. Use at the start of a task to check cached facts before exploring the repo, and log discoveries/mistakes as you go so future sessions don't repeat the same investigation.
---

# seedbank

A local, offline profiler + fact store. It never guesses what's worth
remembering from a single encounter — it tracks how often something gets
rediscovered (or how costly a mistake was) and only promotes a fact to
persistent context when you (the agent) deliberately decide it's worth it.
No LLM runs inside the tool: you supply the compact "representation" text;
the tool does the bookkeeping (frequency, cost, hashing, scoring, tiering,
invalidation, `AGENTS.md` generation).

## Workflow

`seedbank` is not on `PATH`: always run it as `<this-skill-dir>/bin/seedbank`
(bare `seedbank <cmd>` below is shorthand for that). It stores state in
`<git repo root>/.seedbank/` regardless of the current directory.

1. **Before exploring**, check what's already cached:
   ```
   cat AGENTS.md                                   # hot facts, always relevant
   <this-skill-dir>/bin/seedbank status
   ```
   If `AGENTS.md` answers your question (build command, a stated invariant,
   a warm-context pointer relevant to your task), use it and skip
   re-discovery entirely.

2. **While working, log discoveries** you had to dig for — reading a file
   specifically to answer a recurring question, searching for a pattern,
   or running a command to figure something out:
   ```
   <this-skill-dir>/bin/seedbank observe read <path> --task "<why you read it>" [--scope <topic>]
   <this-skill-dir>/bin/seedbank observe search "<pattern>" --task "<why>" [--scope <topic>]
   <this-skill-dir>/bin/seedbank observe run --scope <topic> -- <command...>
   ```
   These are cheap to call — just do it when the read/search/run was in
   service of answering a "how does this repo work" question, not for
   every file you touch while implementing. With the plugin installed,
   `Read`/`Grep`/`Glob` are recorded automatically (see below), so only
   log `run`, `mistake` and `fact` by hand.

3. **Log mistakes and invariants explicitly** — you already know the
   semantic fact; seedbank just needs the text:
   ```
   <this-skill-dir>/bin/seedbank observe mistake "Do not edit src/generated/**; regenerate with tools/codegen.py." \
     --scope invariants --source src/generated/some_file.cpp
   <this-skill-dir>/bin/seedbank observe fact "Never weaken convergence tolerances to make tests pass; tolerance is part of the numerical accuracy contract." \
     --scope invariants
   ```
   `mistake` costs more (it represents wasted work) and ranks higher for
   promotion than a plain discovery.

4. **Check candidates periodically** (end of task, or when asked to update
   project docs):
   ```
   <this-skill-dir>/bin/seedbank candidates
   ```
   Ranks repeated-access keys by estimated value. A correctness/safety
   invariant is always worth promoting regardless of its ranked value —
   use `--critical` when you promote it so it's protected from budget-based
   eviction:
   ```
   <this-skill-dir>/bin/seedbank promote <key> --representation "<compact text>" --tier hot --critical
   <this-skill-dir>/bin/seedbank promote <key> --tier warm --scope solver   # non-critical, topic-scoped
   ```
   Value counts *distinct sessions* that rediscovered a key and decays
   with a 30-day half-life. Promote refuses a fact that contradicts one
   already in the same scope and shows the conflicting fact; re-run with
   `--replace <id>` (retire the old one) or `--force` (keep both).

5. **Regenerate `AGENTS.md`** after promoting/demoting:
   ```
   <this-skill-dir>/bin/seedbank compile
   ```
   This also re-runs invalidation, so a stale fact (its source file changed
   since promotion) is automatically excluded rather than silently served
   as if still true — except `--critical` facts, which stay published and
   are marked "(source changed — verify)". Only the region between
   `<!-- seedbank:begin -->`/`<!-- seedbank:end -->` is rewritten; hand-written
   text outside it is kept. On a fresh clone (empty store) compile refuses to
   wipe an existing block: run `import AGENTS.md` and re-promote first.

6. **Migrating an existing hand-written `AGENTS.md`/`CLAUDE.md`?**
   ```
   <this-skill-dir>/bin/seedbank import <path>
   ```
   Seeds each bullet as a review-before-trusting candidate (lower
   confidence) rather than promoting it outright.

## Automatic observation (Claude Code hook)

The context-garden plugin ships `hooks/hooks.json`, which runs
`bin/seedbank hook` asynchronously after every `Read`, `Grep` and `Glob`
(background, silent, always exits 0, only inside a git repo). If you copied just this skill, add to
`.claude/settings.json` (or `~/.claude/settings.json`):
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
  pointed to from `AGENTS.md` but not inlined — open one only when the
  current task is actually about that topic.
- **Cold** = everything else; just the repository itself.

## In the target repository

Commit `AGENTS.md` (and any `.seedbank/warm/*.md`) — that's the whole
point, it replaces a hand-maintained context file. A new store is created
with `.seedbank/.gitignore` ignoring the local profiler state
(`observations.jsonl`, `keystats.json`, `facts.json`, `config.json`,
`.lock`, `hook.log`, `*.tmp`) but not `warm/`; commit that `.gitignore`
too, and edit it only if the team decides to share observation history.
An existing `.seedbank/.gitignore` is never overwritten.

**If Claude Code is one of the agents working in this repository**, also
commit a one-line `CLAUDE.md`:
```
@AGENTS.md
```
Claude Code reads `CLAUDE.md`, never `AGENTS.md` — no automatic fallback,
confirmed in Anthropic's own docs. The `@AGENTS.md` import line makes
Claude Code load the same facts every other AGENTS.md-reading agent
already gets, without a second copy to keep in sync (`seedbank compile`
only ever needs to touch `AGENTS.md`; this stub never changes again). A
symlink (`ln -s AGENTS.md CLAUDE.md`) works too but fails on Windows
without admin/Developer Mode — the import line doesn't have that problem
and is the officially documented approach either way.

## Guarantees

- Nothing is promoted just because it was seen once — promotion is always
  a deliberate `seedbank promote` call.
- Every fact links back to the source file(s) it came from (when it has
  any); if those files change, the fact is marked stale and excluded from
  `compile` output (or, if `--critical`, kept with a "verify" mark) until
  revalidated (`seedbank invalidate --confirm <id>`) or demoted.
- All scoring/bookkeeping is deterministic (no model calls) — see
  `references/scoring.md` for the exact formulas and
  `references/schema.md` for the full CLI/record reference.
