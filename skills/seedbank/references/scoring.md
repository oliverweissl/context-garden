# Promotion scoring

```
value = ((distinct_sessions - 1) * avg_retrieval_cost + total_failure_cost)
        * stability * decay / max(1, persistent_token_cost)

decay = 0.5 ** (days_since_last_access / half_life_days)     # 1.0 for --critical facts
```

Implemented in `scripts/sb_scoring.py`. Heuristic ranking, not measured cost.

- **`distinct_sessions`** — distinct session ids that observed the key:
  `--session`, else `$CLAUDE_CODE_SESSION_ID` (or `$CLAUDE_SESSION_ID`),
  else today's date + terminal session id; the hook uses the hook JSON's
  `session_id`. Only the last 32 ids are kept, so an id that fell out of
  that window counts again. Repeat reads within one session count once.
- **`distinct_sessions - 1`** — only rediscoveries count: a single read of a
  10k-token file scores 0.
- **`avg_retrieval_cost`** — `total_retrieval_cost / access_count`:
  `read` = file chars/4, `search` = `--cost` or 30, `run` = captured output
  chars/4, `mistake`/`fact` = 0.
- **`total_failure_cost`** — summed per occurrence: `mistake` 300
  (`--failure-cost`), failed `run` 150, `fact` `fact_failure_cost` (60,
  `config.json`), so a never-rediscovered invariant still scores > 0.
- **`stability`** — `1 / (1 + hash_changes)` of the key's sources.
- **`decay`** — `half_life_days` (default 30) since last access. For
  promoted facts (hot-budget eviction), promote and `invalidate --confirm`
  count as an access.
- **`persistent_token_cost`** — `chars(representation) / 4`; 20 for a
  candidate without a representation.

## Threshold

`candidates` lists keys with `value >= promote_threshold` (`config.json`,
default 1.0). At the default: a ~30-token file rediscovered in 2 sessions
≈ 1.6; a compact logged mistake ≫ 1; a declared fact ≈ `60 / tokens`,
dropping below 1 after about two half-lives without access.

## Worked example (from `tests/fixtures/mini_repo`)

- `README.md` (~32 tok) read in 5 sessions: `(4 * 32 + 0) / 20 = 6.4`.
  `big.txt` (10k tok) read once: `(0 * 10000 + 0) / 20 = 0`.
- Mistake logged 10x (~14 tok): `3000 / 14 ≈ 214`; logged once: `≈ 21`.
