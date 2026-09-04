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
  config.json             {hot_budget, promote_threshold}
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
| `mistake <representation>` | `mistake:<sha1(representation)[:12]>` | 0 | `--failure-cost` or 300 |
| `fact <representation>` | `fact:<sha1(representation)[:12]>` | 0 | `--failure-cost` or 0 |

All kinds accept `--scope <topic>` (default `general` for read/search/run,
`invariants` for `mistake`, `general` for `fact`) and `--source <path>`
(repeatable; `read`/`run` don't take `--source` since their subject *is*
the source/command). Every `observe` call increments `access_count` for
its key and updates each listed source's content hash — a hash that
differs from the previously recorded one for that source increments
`hash_changes` (this is what `stability` is computed from later).

`run` actually executes the command (stdout+stderr captured, merged) and
prints it back (truncated past 2000 chars) — it does **not** store the raw
output the way compost does; if you need full raw-output retrieval for a
large/repetitive command, use compost for that command and
`seedbank observe run` for the fact that you had to run it at all.

## `seedbank candidates [--threshold N] [--all] [--json]`

Lists every key without an already-active (non-stale) fact, sorted by
descending `value` (see `references/scoring.md`). Default threshold comes
from `config.json` (`promote_threshold`, default 1.0). `--all` ignores the
threshold. A candidate's `representation` is `null` until either an
`observe fact`/`mistake` call supplied one for that key, or you pass one
explicitly at promote time.

## `seedbank promote <key> [--representation TEXT] [--tier hot|warm] [--scope S] [--critical] [--budget N]`

Creates a new fact from `key`'s aggregate stats (falls back to the
observation-supplied representation if `--representation` is omitted;
errors if neither exists). `sources`/`source_hashes` are copied from the
key's current aggregate. `--critical` sets `protected: true`, which
exempts the fact from value-based eviction (it can still be removed
manually with `demote --tier cold`). Promoting to `hot` past the token
budget auto-demotes the lowest-value non-critical hot fact(s) to `warm`
until it fits; if nothing is left to evict, promotion is refused with a
clear error (raise `--budget`, demote something, or use `--tier warm`).

Note: promoting the same `key` twice creates two separate facts (the tool
does not currently detect "this key already has an active fact and should
be updated in place" as a special case) — demote or remove the old one
first if you're re-promoting with a revised representation.

## `seedbank demote <fact_id> --tier warm|cold`

`--tier warm` just changes the tier field. `--tier cold` deletes the fact
outright (this is the only way to remove a fact in the current CLI).

## `seedbank invalidate [--confirm FACT_ID]`

No args: re-hashes every fact's sources; a fact with any source whose hash
no longer matches (or that no longer exists on disk) is marked
`stale: true` with a `stale_reason`. Facts with no sources (pure policy
statements, e.g. the UC3 tolerance invariant) are never marked stale by
this scan — there's nothing to check. `compile` always runs this scan
first and excludes stale facts from its output.

`--confirm FACT_ID`: re-hashes that fact's sources, clears `stale`,
bumps `confidence` by +0.05 (capped at 1.0), and sets `last_verified`. Use
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

## `seedbank compile [--targets AGENTS.md,CLAUDE.md,...]`

Runs invalidation, then writes the hot section + warm-scope pointers to
each target file (default: just `AGENTS.md`) and one `.seedbank/warm/<scope>.md`
per warm scope that has at least one active fact.

## `seedbank status` / `seedbank stats [--json]`

`status`: tracked-key count, active/hot/warm/stale fact counts, hot-budget
usage, candidate count above threshold, and a list of stale facts needing
attention. `stats`: per-fact and total **estimated tokens avoided** —
defined as `max(0, retrieval_cost_accumulated_before_promotion -
persistent_token_cost)`, i.e. the rediscovery cost that was already sunk
before promotion, now amortized into one small persistent fact. This is a
conservative, already-realized number; it does not project future savings
(see `validate.md` for why, and for the honest limitation this implies).

## `seedbank import <file>`

Splits a markdown-ish file into candidates: a `#`/`##` heading sets the
`scope` for subsequent bullets, each `- `/`* ` line becomes one `imported`
candidate (deduplicated by text hash). No representation is trusted
outright — review with `seedbank candidates` and promote deliberately.
