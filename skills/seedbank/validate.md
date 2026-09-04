# Validating seedbank

seedbank's job is: stop agents from re-discovering the same repository
knowledge every session, without ever serving a fact that's gone stale, and
without bloating the always-loaded context. Validation checks four things:
**observation correctness** (do repeated accesses actually accumulate),
**scoring sanity** (does the ranking match intuition — a repeated mistake
should outrank a repeated read), **fidelity** (stale facts never leak into
`AGENTS.md`), and **behavior change** (does an agent actually stop
re-discovering things once they're promoted).

## 1. Automated checks (run this first, every time the code changes)

```
bash tests/smoke_test.sh
```

Expected: `ALL CHECKS PASSED`, 20/20 checks, against `tests/fixtures/mini_repo`
(copied to a scratch dir per run so the fixture itself stays clean — never
run manual `seedbank` commands directly inside `tests/fixtures/mini_repo`;
use a copy). It exercises:

- **UC1 (build discovery)**: 3 repeated `read README.md` observations
  accumulate `access_count=3`.
- **UC2 (repeated mistake)**: 2 `mistake` observations on the same
  generated file accumulate, and — this is the important check — **rank
  higher than the UC1 read** despite fewer repeats, because a mistake's
  default failure cost (300) dominates a read's retrieval cost (~32
  tokens). See `references/scoring.md` for the worked numbers.
- **UC3 (scientific invariant)**: a `fact` observation with no backing
  source promotes and compiles cleanly even though its *value score is
  0.00* (no repeated cost was ever measured) — this is by design, not a
  bug; see the "What the score deliberately does NOT decide" section in
  `references/scoring.md`.
- **Manual promotion always available regardless of score** — `promote`
  never checks the candidate threshold.
- **compile** reproduces all three spec worked examples verbatim in
  `AGENTS.md`, and the compiled file's word count stays within the
  default hot budget.
- **Invalidation lifecycle**: changing a fact's backing source triggers
  `stale: true`; `compile` excludes it from `AGENTS.md` (checked
  explicitly — the stale text must NOT appear in the output);
  `invalidate --confirm <id>` restores it.
- **Eviction**: promoting past a tight hot budget demotes the
  lowest-value non-`--critical` fact to warm, while `--critical` facts
  survive untouched.
- **gc**: pruning `observations.jsonl` never loses the aggregate
  `access_count` in `keystats.json`.
- **import**: a heading + bullet file seeds one candidate per bullet with
  scope taken from the nearest heading.

If you add a new `observe` kind or change scoring, add a fixture-driven
assertion here before considering the change done.

## 2. Fidelity checks against the spec's acceptance criteria

| Criterion (from spec) | How to check | Status |
|---|---|---|
| Records repository accesses | `seedbank observe read/search/run` append to `observations.jsonl` and update `keystats.json` | met |
| Identifies repeated retrieval paths | `access_count` per key visible via `seedbank candidates`/`status` | met |
| Produces semantic cache candidates | `seedbank candidates` ranks unpromoted keys by value | met |
| Tracks evidence hashes | `source_hashes` per key/fact, `sha256_file` (16 hex chars) | met |
| Supports hot/warm tiers | `--tier hot\|warm` on promote; `compile` renders them differently (inline vs. pointer file) | met |
| Automatically invalidates stale entries | `compile` always re-runs the invalidation scan first; smoke test proves a changed source excludes the fact | met (auto on every compile, not a background watcher — see limitations) |
| Enforces a configurable token budget | `config.json: hot_budget`, enforced + auto-evicted at `promote`/`gc` time | met |
| Generates `AGENTS.md` | `seedbank compile` (default target) | met |
| Reports estimated tokens avoided | `seedbank stats` | met, conservatively (see schema.md's definition) |
| hot context ≤500 tokens by default | fixture's 3-fact `AGENTS.md` = 81 words; default `hot_budget=500` enforced at promote time | met |
| <1% stale fact exposure after relevant source modification | smoke test explicitly asserts the stale fact's text is ABSENT from post-invalidation `AGENTS.md` (0%, on this fixture) | met on fixture; see limitations for what this doesn't cover |
| measurable reduction in repeated agent mistakes | UC2 mistake-observation flow + `--critical` promotion makes the invariant visible in hot context on every future session | mechanism verified; real-world reduction requires the manual behavioral check below |

## 3. Manual behavioral check (does an agent actually stop re-discovering things)

Automated checks prove the CLI's bookkeeping is correct; they don't prove
an agent will actually call `seedbank observe`/consult `AGENTS.md` instead
of re-exploring the repo from scratch. Do this once per significant
`SKILL.md` change:

1. In a scratch repo (or reuse `tests/fixtures/mini_repo`), do **not**
   install this skill. Ask an agent, across 3 separate fresh sessions, "how
   do I build this project?" Note whether it re-reads `README.md`/
   `CMakeLists.txt` every single time (expected: yes, every time — no
   memory between sessions) and the tool-result token cost of each.
2. Install `seedbank/` where the agent can discover it, and run
   `seedbank promote`+`compile` once to seed `AGENTS.md` with the build
   command (or let the agent do it after session 1, per the SKILL.md
   workflow). Repeat the same prompt in fresh sessions 2 and 3.
3. Confirm: session 2+ answers from `AGENTS.md` alone, with no file reads
   for the build command. Compare tool-result token cost across the two
   conditions — expect a large reduction from session 2 onward, with the
   answer still correct (if the cached build command is wrong or outdated,
   that's a correctness regression, not an acceptable tradeoff — fix
   invalidation/promotion before accepting any token savings).
4. Separately, verify the "prevents repeated mistakes" mechanism (UC2):
   ask an agent to make an unrelated change in a repo where
   `src/generated/**` is marked as a promoted, `--critical` invariant.
   Confirm it does not attempt to hand-edit the generated file.

## 4. Known limitations (accepted for MVP, revisit if they cause failures)

- **Invalidation is checked eagerly at read time (`status`, `candidates`,
  `compile`), not via a background watcher.** A fact can theoretically be
  served stale if something reads `facts.json` directly instead of going
  through `compile`. In practice the only agent-facing artifact is
  `AGENTS.md`, which is always freshly compiled with an invalidation pass,
  so this is a narrow gap (documented, not hidden).
- **No automatic promotion.** Per the phased rollout, `seedbank candidates`
  only ranks and suggests; a human/agent always decides. This is
  intentional for the MVP (revisit only after collecting enough real
  telemetry to trust an automatic threshold), not an oversight.
- **Promoting an already-active key creates a second fact rather than
  updating in place.** Demote the old one first when revising wording.
- **The value score has no signal for "this matters even though it was
  never expensive to rediscover"** (true of most correctness/safety
  invariants) — that's why `--critical` promotion is always available
  independent of score, and why `seedbank stats`' "tokens avoided" will
  correctly show near-zero for such facts. Don't mistake a low score for
  "not worth promoting."
- **`seedbank observe run` does not store raw command output** the way
  compost does — it only tracks cost/failure for scoring purposes. Pair
  it with compost for commands whose full output might be needed later.
- **Token costs are a chars/4 estimate**, not a real tokenizer. Fine for
  ranking (everything uses the same estimator, so relative order is
  stable) but don't quote `stats` numbers as exact.
