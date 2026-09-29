# CLI reference

Store state is plain JSON under `.seedbank/`; safe to hand-edit between CLI
calls (each call does a full load/mutate/save).

## `seedbank observe <kind> ...`

Kinds: `read <path>`, `search <pattern>`, `run -- <cmd>`,
`mistake <text>`, `fact <text>`. Costs per kind: `references/scoring.md`.

`--scope` defaults to `invariants` for `mistake`, else `general`.
`--source <path>` (repeatable) applies to `mistake`/`fact` only; a
source whose content hash changed since the last observation increments
`hash_changes` (lowers `stability`). `mistake`/`fact` keys hash the
normalised text (case, whitespace, trailing punctuation), so trivial
variants share a key. `read` of a missing path is an error and records
nothing.

`run` executes the command and echoes merged stdout+stderr (truncated past
2000 chars) but does **not** store the raw output; for large/repetitive
output use compost for the command and `observe run` only to record that
you had to run it. `--timeout SECONDS` (default 600) kills a hung command,
recorded as failed (exit 124, `timed_out: true`).

## `seedbank candidates [--threshold N] [--all] [--json]`

Keys without a fact, sorted by `value`; threshold defaults to
`promote_threshold` (1.0), `--all` ignores it. `representation` is `null`
unless an `observe fact`/`mistake` supplied one; then pass
`--representation` at promote time.

## `seedbank promote <key> [--representation TEXT] [--tier hot|warm] [--scope S] [--critical] [--budget N] [--force] [--replace FACT_ID]`

`--tier` defaults to `warm`. Errors if no representation is available.
Sources are copied from the key and hashed at promote time. `--critical`
exempts the fact from eviction and decay. Promoting to `hot` past the
budget auto-demotes the lowest-value non-critical hot facts to `warm`;
if nothing can be evicted, promotion is refused (raise `--budget`, demote
something, or use `--tier warm`). Stale `--critical` facts
still count against the budget (they stay published). A key that already
has a fact is refused unless `--force`.

**Contradictions.** A new fact whose content words overlap an existing
same-scope fact (Jaccard >= 0.4) with opposite polarity (exactly one side
says never/not/no/don't/avoid/cannot...) is refused, listing the
conflicts. `--replace FACT_ID` deletes that fact and promotes the new one;
`--force` keeps both. Lexical only: antonyms like tabs vs spaces are missed.

## `seedbank hook`

Claude Code `PostToolUse` entry point for `Read|Grep|Glob` (setup:
`references/guide.md`). Skips files outside the repo and under `.git/` or
`.seedbank/`; errors go to `.seedbank/hook.log`.

## `seedbank demote <fact_id> --tier warm|cold`

`--tier cold` **deletes** the fact.

## `seedbank invalidate [--confirm FACT_ID]`

No args: marks `stale` every fact with a source whose hash changed or that
no longer exists. Facts without sources are never marked stale. `compile`
runs this first.

`--confirm FACT_ID`: after you've reviewed a stale fact (optionally
editing its text in `facts.json`), re-hashes its sources, clears `stale`,
bumps `confidence` +0.05 and returns it to the published set (hot budget
re-enforced).

## `seedbank gc [--purge-stale] [--keep-per-key N]`

Re-applies hot-budget eviction (e.g. after lowering `hot_budget` in
`config.json`), with `--purge-stale` deletes stale facts, and prunes the
raw observation log to the last N (default 5) entries per key. Aggregate
stats are kept, so scores are unaffected.

## `seedbank compile [--targets AGENTS.md,...] [--force]`

Writes the hot section + warm-scope pointers to each target (default
`AGENTS.md`) and `.seedbank/warm/<scope>.md` per warm scope with an active
fact. With an empty store it refuses to wipe an existing seedbank block
(fresh clone); `--force` overrides. For Claude Code, prefer the
`CLAUDE.md` import over adding it to `--targets` (`references/guide.md`).

## `seedbank status` / `seedbank stats [--json]`

`status`: counts, hot-budget usage, candidates above threshold, stale
facts. `stats`: estimated tokens avoided per fact =
`max(0, retrieval cost before promotion - persistent_token_cost)`; no
future projection.

## `seedbank import <file>`

`#`/`##` headings set the scope; each `- `/`* ` bullet becomes one
`imported` candidate (deduplicated). Review with `candidates` and promote
deliberately.
