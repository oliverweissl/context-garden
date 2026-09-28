# Promotion scoring

```
value = ((distinct_sessions - 1) * avg_retrieval_cost + total_failure_cost)
        * stability * decay / max(1, persistent_token_cost)

decay = 0.5 ** (days_since_last_access / half_life_days)     # 1.0 for --critical facts
```

Implemented in `scripts/sb_scoring.py`. Every term is a heuristic, not a
measured probability or true cost — the goal is a consistent, explainable
ranking, not an optimal control policy.

- **`distinct_sessions`** — number of distinct session ids that observed
  the key. The session id is `--session`, else `$CLAUDE_CODE_SESSION_ID`
  (exported by Claude Code to Bash tool calls; `$CLAUDE_SESSION_ID` is
  also honoured), else today's date + the terminal's OS session id. The
  PostToolUse hook uses the `session_id` field of the hook JSON. Each key
  stores `session_count` plus the last 32 ids (`sessions`); an id that has
  fallen out of that window counts again if it reappears. Rereading the
  same file five times in *one* session counts once: the question is
  whether knowledge is *re*discovered across sessions, not how often a
  file is touched while implementing.
- **`distinct_sessions - 1`** — the first discovery is sunk cost; only
  later rediscoveries are what a persisted fact would have saved. So a
  single read of a 10k-token file scores 0 from retrieval, while a
  32-token file rediscovered in 5 sessions scores `4 * 32`.
- **`avg_retrieval_cost = total_retrieval_cost / access_count`** — tokens
  spent per rediscovery (file size for `read`, flat/given cost for
  `search`, captured-output size for `run`; 0 for `mistake`/`fact`).
- **`total_failure_cost`** — *summed*, not averaged: a mistake made 10x
  costs 10x a mistake made once. `mistake` defaults to 300 per
  occurrence (`--failure-cost`), a failed `run` to 150. A declared `fact`
  defaults to `fact_failure_cost` from `config.json` (60) per declaration,
  so a stated invariant that was never "rediscovered" (e.g. the UC3
  tolerance contract) still gets a nonzero base value instead of 0.
- **`stability = 1 / (1 + hash_changes)`** — a fact whose backing file
  keeps changing between observations is less trustworthy to persist.
- **`decay`** — exponential forgetting with `half_life_days` (config,
  default 30) since the key's last access. A declared fact of ~25 tokens
  (value ≈ 2.4) stays above the default threshold after one half-life and
  falls below it after two. For promoted facts (hot-budget eviction),
  promote / `invalidate --confirm` count as an access, live keystats are
  used when the key is still tracked, and `--critical` facts never decay.
- **`persistent_token_cost`** — `chars(representation) / 4`; 20-token
  placeholder for an unpromoted candidate without a representation.

## Threshold

`seedbank candidates` lists keys with `value >= promote_threshold`
(`config.json`, default 1.0). Rough guide at the default: a ~30-token file
rediscovered in 2 sessions ≈ 1.6; any logged mistake with a compact
representation ≫ 1; a declared fact ≈ `60 / tokens`, fading below 1 after
about two half-lives without access. Columns: `value`, `sess` (distinct
sessions), `n` (accesses), `decay`, `kind`, `scope`, `key`.

## Legacy stores

Keys written before session tracking (no `session_count`) count each past
access as its own session, and a legacy declared `fact` with zero failure
cost is scored as one declaration at `fact_failure_cost`. The next
`observe` of such a key migrates it in place. A `config.json` missing
`half_life_days`/`fact_failure_cost` falls back to the defaults.

## What the score deliberately does NOT decide

`promote` never requires a candidate to clear the threshold — promotion
is always deliberate. Correctness/safety invariants should be promoted
with `--critical` on the strength of what they say, not how often they
were rediscovered; `--critical` also exempts them from decay and eviction.

## Worked example (from `tests/fixtures/mini_repo`)

- `README.md` (~32 tok) read in 5 sessions: `(4 * 32 + 0) / 20 = 6.4`.
  `big.txt` (10k tok) read once: `(0 * 10000 + 0) / 20 = 0`.
- Mistake logged 10x (~14 tok): `3000 / 14 ≈ 214`; logged once: `≈ 21`.
