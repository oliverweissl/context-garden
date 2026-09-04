# Promotion scoring

```
value = (reuse_probability * avg_retrieval_cost + avg_failure_cost) * stability
        / persistent_token_cost
```

Implemented in `scripts/sb_scoring.py`. Every term is a heuristic, not a
measured probability or true cost — the goal is a consistent, explainable
ranking to prioritize a human/agent's attention, not an optimal control
policy. Concretely, for a key with `n = access_count` observations:

- **`reuse_probability = n / (n + 2)`** — a Laplace-smoothed frequency
  ratio. `n=1` gives 0.33, `n=3` gives 0.6, `n=10` gives 0.83, asymptotic
  to 1.0. Chosen so a single occurrence doesn't already look "certain to
  recur" but a handful of repeats climbs quickly.
- **`avg_retrieval_cost = total_retrieval_cost / n`** — average tokens
  spent rediscovering this per access (file size for `read`, a flat/given
  cost for `search`, captured-output size for `run`; always 0 for
  `mistake`/`fact`, since those aren't "rediscovery" events).
- **`avg_failure_cost = total_failure_cost / n`** — average wasted-work
  cost per access. Zero for clean reads/searches/successful runs; nonzero
  for failed `run`s and always-nonzero-by-default for `mistake` (300,
  configurable via `--failure-cost`) since a mistake represents a full
  edit-revert-debug cycle, not just a re-read.
- **`stability = 1.0` if no source hash has ever changed across this
  key's observations (or the key has no sources at all), else
  `1 / (1 + hash_changes)`** — a fact whose backing file keeps changing
  underneath repeated observations is less trustworthy to persist as-is.
- **`persistent_token_cost`** — `chars(representation) / 4`, i.e. the
  ongoing cost of keeping this fact loaded. For an unpromoted candidate
  with no representation yet, a fixed placeholder (20 tokens) is used
  purely so it can be ranked at all.

## Why this shape

The numerator approximates "expected benefit of never having to redo this
work again, weighted by how much we trust it stays true." The denominator
is "what does it cost, every time this context loads, to keep the shortcut
around." Dividing by `persistent_token_cost` is what makes the score
reward *compact* representations over verbose ones for the same benefit —
consistent with the project-wide goal of maximizing verified results per
token, not just recording everything that was ever useful once.

## What the score deliberately does NOT decide

`seedbank candidates` ranks by this score, but `seedbank promote` never
requires a candidate to clear the threshold — promotion is always a
deliberate, manual action (per the project's phased rollout: automatic
promotion is explicitly deferred until there's enough telemetry to trust
it). This matters most for UC3-style scientific/safety invariants: a
tolerance-contract statement logged once via `observe fact` scores `0.00`
under this formula (no repeated cost was ever measured), but it should
still be promoted with `--critical` on the strength of *what it says*, not
how often it was rediscovered. The score is a prioritization aid for the
common case (build commands, conventions that really were rediscovered
repeatedly), not a gate on judgment calls about correctness-critical
knowledge — that's also why `--critical` exists as an eviction override
independent of score.

## Worked example (from `tests/fixtures/mini_repo`)

- Read `README.md` 3x looking for the build command: `n=3`,
  `avg_retrieval_cost≈32` tok, `avg_failure_cost=0`, `stability=1.0`
  (file never changed), placeholder `persistent_token_cost=20`.
  `value = (0.6 * 32 + 0) * 1.0 / 20 ≈ 0.96`.
- Edit-then-revert `src/generated/bindings.cpp` twice, logged as
  `mistake`: `n=2`, `avg_retrieval_cost=0`, `avg_failure_cost=300`,
  `stability=1.0`. Once a representation is supplied
  (`persistent_token_cost≈16`): `value = (0.5*0 + 300) * 1.0 / 16 ≈ 18.75`.
  The mistake outranks the read by ~20x, which matches intuition: a wasted
  edit cycle is far more expensive than a re-read, even at half the
  repeat count.
