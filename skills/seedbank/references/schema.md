# CLI and record reference

## Storage layout

```
.seedbank/
  observations.jsonl   append-only raw event log (audit trail; safe to
                        prune with `gc` -- aggregates live elsewhere)
  keystats.json         {key: aggregate stats}, one entry per distinct
                        repeated-access key, updated incrementally on
                        every `observe` call
  facts.json             {seq, facts: {fact_id: {...}}} -- promoted facts
  config.json             {hot_budget, promote_threshold, half_life_days,
                          fact_failure_cost} (missing keys -> defaults)
  hook.log                errors swallowed by the Claude Code hook
```

Everything is plain JSON, human-readable, safe to inspect or hand-edit
between CLI calls (the CLI always does a full load/mutate/save, never a
partial patch).

## `seedbank observe <kind> ...`

| kind | identity (`key`, unless `--key` given) | retrieval cost | failure cost |
|---|---|---|---|
| `read <path>` | `read:<path>` | tokens in the file (chars/4) | 0 |
| `search <pattern>` | `search:<pattern>` | `--cost` or a flat 30 | 0 |
| `run -- <cmd>` | `run:<cmd>` | tokens in captured stdout+stderr | 150 if exit != 0, else 0 |
| `mistake <representation>` | `mistake:<sha1(norm(representation))[:12]>` | 0 | `--failure-cost` or 300 |
| `fact <representation>` | `fact:<sha1(norm(representation))[:12]>` | 0 | `--failure-cost` or `fact_failure_cost` (60) |

All kinds accept `--scope <topic>` (default `general` for read/search/run,
`invariants` for `mistake`, `general` for `fact`) and `--source <path>`
(repeatable; `read`/`run` don't take `--source` since their subject *is*
the source/command). Every `observe` call increments `access_count` for
its key, adds the current session id (global `--session`, else
`$CLAUDE_CODE_SESSION_ID`, else date + terminal session) to the key's
`sessions`/`session_count`, and updates each listed source's content hash — a hash that
differs from the previously recorded one for that source increments
`hash_changes` (this is what `stability` is computed from later).

`norm()` lowercases, collapses whitespace and strips trailing punctuation,
so trivial variants share a key; the stored `representation` keeps the
text as written. A store that already tracks the older raw-text key
(`sha1(representation)`) for that text keeps using it. `read` of a path
that doesn't exist is an error and records nothing.

`run` actually executes the command (stdout+stderr captured, merged) and
prints it back (truncated past 2000 chars) — it does **not** store the raw
output the way compost does; if you need full raw-output retrieval for a
large/repetitive command, use compost for that command and
`seedbank observe run` for the fact that you had to run it at all.
`--timeout SECONDS` (default 600) kills a hung command; it is recorded as
failed (`exit_code` 124, `timed_out: true`).

## `seedbank candidates [--threshold N] [--all] [--json]`

Lists every key without an existing fact (stale ones included), sorted by
descending `value` (see `references/scoring.md`). Default threshold comes
from `config.json` (`promote_threshold`, default 1.0). `--all` ignores the
threshold. Output columns: `value`, `sess` (distinct sessions), `n`
(accesses), `decay`; `--json` adds `distinct_sessions` and `decay` fields.
A candidate's `representation` is `null` until either an
`observe fact`/`mistake` call supplied one for that key, or you pass one
explicitly at promote time.

## `seedbank promote <key> [--representation TEXT] [--tier hot|warm] [--scope S] [--critical] [--budget N] [--force] [--replace FACT_ID]`

`--tier` defaults to `warm`.

Creates a new fact from `key`'s aggregate stats (falls back to the
observation-supplied representation if `--representation` is omitted;
errors if neither exists). `sources` are copied from the key's current
aggregate and hashed at promote time. `--critical` sets `protected: true`, which
exempts the fact from value-based eviction (it can still be removed
manually with `demote --tier cold`). Promoting to `hot` past the token
budget auto-demotes the lowest-value non-critical hot fact(s) to `warm`
until it fits; if nothing is left to evict, promotion is refused with a
clear error (raise `--budget`, demote something, or use `--tier warm`).

Hot budget accounting includes stale `--critical` facts (they are still
published). Promoting a `key` that already has a fact is refused;
`demote <id> --tier cold` it first, or pass `--force` to replace it.

**Contradictions.** Before promoting, the new text is compared with every
existing fact in the same scope: normalised content words (lowercased,
stopwords and negations removed, crude plural folding) with Jaccard
overlap >= 0.4 *and* opposite polarity (exactly one side contains
never / not / no / don't / do not / avoid / cannot / ...) is a conflict.
Promotion is then refused, listing each conflicting fact id and text.
`--replace FACT_ID` retires that fact (demote to cold) and promotes the new
one; `--force` keeps both. The check is lexical: antonym pairs such as
tabs vs spaces are not detected. (0.4 rather than 0.3: at 0.3, e.g. "Use
ninja for builds" vs "Do not use in-source builds" was a false positive.)

## `seedbank hook`

Claude Code `PostToolUse` entry point (wired by the plugin's
`hooks/hooks.json`, matcher `Read|Grep|Glob`). Reads the hook JSON on
stdin and records `Read` as `observe read <repo-relative path>`, `Grep` as
`observe search "<pattern>[ path=<dir>][ glob=<glob>]"`, `Glob` as
`observe search "<pattern>[ path=<dir>]"`, under the hook's `session_id`.
Relative paths resolve against the hook's `cwd`; files outside the git
repo and anything under `.git/` or `.seedbank/` are skipped. Acts only
inside a git work tree, creates the store lazily, prints nothing, always
exits 0 (errors go to `.seedbank/hook.log`), ~50 ms per call. Registered
with `"async": true`, so it runs in the background and never delays the
tool call.

## `seedbank demote <fact_id> --tier warm|cold`

`--tier warm` just changes the tier field. `--tier cold` deletes the fact
outright (this is the only way to remove a fact in the current CLI).

## `seedbank invalidate [--confirm FACT_ID]`

No args: re-hashes every fact's sources; a fact with any source whose hash
no longer matches (or that no longer exists on disk) is marked
`stale: true` with a `stale_reason`. Facts with no sources (pure policy
statements, e.g. "never weaken a convergence tolerance") are never marked stale by
this scan — there's nothing to check. `compile` always runs this scan
first and excludes stale facts from its output.

`--confirm FACT_ID`: re-hashes that fact's sources, clears `stale`,
bumps `confidence` by +0.05 (capped at 1.0), sets `last_verified`, and
re-enforces the hot budget (the fact re-enters the published set). Use
this once you've reviewed a stale fact and confirmed it's still accurate
(possibly after updating its representation by hand in `facts.json`, or by
demoting and re-promoting with corrected text).

## `seedbank gc [--purge-stale] [--keep-per-key N]`

Runs the same eviction-to-budget logic as `promote` (in case `config.json`
was edited to lower `hot_budget` after facts were already promoted),
optionally deletes facts still marked stale (`--purge-stale`), and prunes
`observations.jsonl` down to the last `N` (default 5) raw entries per key
— aggregates in `keystats.json` are untouched by this, so `access_count`
etc. are never lost.

## `seedbank compile [--targets AGENTS.md,CLAUDE.md,...] [--force]`

Runs invalidation, then writes the hot section + warm-scope pointers to
each target file (default: just `AGENTS.md`) and one `.seedbank/warm/<scope>.md`
per warm scope that has at least one active fact. With an empty store it
refuses to wipe an existing seedbank block (fresh clone); `--force`
overrides that.

For Claude Code specifically, prefer a one-time `@AGENTS.md` import line
in a committed `CLAUDE.md` (see `guide.md`, "In the target repository")
over adding `CLAUDE.md` to `--targets` — Claude Code never reads
`AGENTS.md` on its own, and the import keeps one source of truth instead
of two full copies that both need regenerating on every `compile`.

## `seedbank status` / `seedbank stats [--json]`

`status`: tracked-key count, active/hot/warm/stale fact counts, hot-budget
usage, candidate count above threshold, and a list of stale facts needing
attention. `stats`: per-fact and total **estimated tokens avoided** —
defined as `max(0, retrieval_cost_accumulated_before_promotion -
persistent_token_cost)`, i.e. the rediscovery cost that was already sunk
before promotion, now amortized into one small persistent fact. This is a
conservative, already-realized number; it does not project future savings.

## `seedbank import <file>`

Splits a markdown-ish file into candidates: a `#`/`##` heading sets the
`scope` for subsequent bullets, each `- `/`* ` line becomes one `imported`
candidate (deduplicated by text hash). No representation is trusted
outright — review with `seedbank candidates` and promote deliberately.
