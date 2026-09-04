# Validating compost

compost's job is: never let a large/repetitive command output flood an
agent's context, while never losing the ability to recover exact raw
evidence or the root cause. Validation therefore checks three things:
**compaction** (did the context actually shrink), **fidelity** (is the raw
output still 100% recoverable and is the root cause still present after
compression), and **behavior change** (does an agent actually use this
instead of reading raw logs).

## 1. Automated checks (run this first, every time the code changes)

```
bash tests/smoke_test.sh
```

Expected: `ALL CHECKS PASSED`, 22/22 checks. It exercises, against bundled
fixtures in `tests/fixtures/`:

- **pytest fixture** (384 lines, 37 failures): confirms the dominant
  failure (27 occurrences) becomes the single root event and the two
  smaller groups (8, 2) become secondary/repeated groups — this is the
  UC1 "20,000 repeated errors -> root + secondary groups" behavior from
  the spec, at fixture scale.
- **gcc fixture** (60 near-identical template-instantiation errors):
  confirms they collapse into one clustered root event, not 60 lines.
- **slurm fixture**: confirms profile auto-detection doesn't
  misfire (SLURM's own `slurmstepd: error:` lines look like GCC diagnostics
  to a naive regex — this is a regression test for a bug caught during
  development) and that OOM/peak-memory extraction works.
- **clean-pass fixture**: confirms no false-positive errors on a
  successful run.
- **solver run1 -> run2 pair**: confirms delta mode reports the residual
  improvement and a resolved warning instead of two full summaries — the
  UC3 "iterative fixing" behavior.
- **lossless recoverability**: `get <id> --lines all` byte-for-byte
  reproduces the original ingested file.
- **error handling**: unknown run/event ids fail with a clean one-line
  message, never a raw Python traceback (an agent should be able to
  recover from a bad id, not get dumped a stack trace into context).
- **exit code propagation**: `compost run`'s own exit code equals the
  wrapped command's exit code, so it composes in shell scripts / CI.

If you add a profile or change clustering logic, add a fixture + assertion
here before considering the change done.

## 2. Fidelity checks against the spec's acceptance criteria

| Criterion (from spec) | How to check | Status |
|---|---|---|
| Raw output never discarded | `ls .compost/runs/<id>/raw.txt` exists for every run; smoke test's recoverability check | met |
| Every event links to raw evidence | `compost event <id> --event N` always resolves to real line numbers in `raw.txt` | met |
| Repeated identical output collapses deterministically | same input -> same clustering every time (pure regex, no randomness); smoke test re-asserts exact counts | met |
| Supports before/after comparison | `changed_since_previous_run` (automatic) + `compost diff <a> <b>` (explicit) | met |
| Root errors survive compression | pytest/gcc fixtures: root cause message present verbatim in the summary | met |
| No LLM required for baseline compaction | `grep -ri` for any HTTP/API-key/model-call code in `scripts/` — there is none; everything is `re`/`json`/`subprocess` stdlib | met |
| ≥80% reduction in tool-result tokens | measured 93.8% (pytest fixture) and 93.7% (gcc fixture) word-count reduction, raw vs. summary — see command below | met, with margin |
| 100% recoverability of raw output | smoke test diffs recovered vs. original fixture | met |
| ≥99% preservation of root-cause errors | every fixture's known root cause appears verbatim in `root_events[0].message` | met on fixture corpus (n=2 error-bearing fixtures; expand corpus before trusting this figure at scale) |

Reproduce the compression measurement:
```
tf() { python3 scripts/compost.py --store /tmp/tf_metrics "$@"; }
raw_words=$(wc -w < tests/fixtures/pytest_fail.log)
summary_words=$(tf ingest --file tests/fixtures/pytest_fail.log --command x --exit-code 1 | wc -w)
echo "reduction: $(python3 -c "print(f'{100*(1-$summary_words/$raw_words):.1f}%')")"
```

## 3. Manual behavioral check (does an agent actually behave differently)

Automated checks prove the CLI is correct; they don't prove an agent will
*use* it instead of dumping raw output into context. Do this once per
significant SKILL.md change:

1. In a scratch repo, create a command that produces >200 lines of
   repetitive output (e.g. reuse `tests/fixtures/pytest_fail.log` behind a
   fake `pytest` shim, or a real flaky test suite).
2. **Baseline**: ask a coding agent (with this skill *not* installed) to
   "run the tests and diagnose the failures." Note whether it pipes raw
   output into its own context, and roughly how many tool-result tokens
   that costs (character count / 4 is a fine estimate).
3. **With skill**: install `compost/` where the agent can discover it
   (skills directory), repeat the same prompt. Confirm the agent:
   - invokes `compost run -- pytest ...` (or `ingest`) rather than a bare
     shell call for the test run,
   - diagnoses correctly from the compact summary alone,
   - only calls `get`/`event`/`grep` for specific follow-up lines, not the
     whole log,
   - on a second, deliberately-provoked re-run, notices and reports the
     delta rather than re-reading everything.
4. Compare tool-result token cost baseline vs. with-skill. Expect a large
   reduction (the fixture-level numbers above are a lower bound — real
   logs with more repetition compress harder) with **no loss of diagnostic
   correctness** (same root cause identified either way). If the agent
   reaches a wrong or less confident diagnosis with compost than
   without, that is a functional regression — fix clustering/parsing
   before shipping, don't just accept the token savings.

## 4. Adding a new profile safely

1. Add a realistic fixture to `tests/fixtures/` (prefer a real captured log
   over a hand-typed one; synthetic is fine if you note in a comment what
   real-world shape it approximates).
2. Add `parse_<name>` to `scripts/co_profiles.py` + a detection branch +
   register in `PARSERS`.
3. Add assertions to `tests/smoke_test.sh` for: correct profile detection,
   at least one correct clustering assertion, and — if the profile has a
   `numerical_summary` — at least one assertion on its fields.
4. Update `references/profiles.md`.
5. Re-run `bash tests/smoke_test.sh`; all checks (old and new) must pass.

## 5. Known limitations (accepted for MVP, revisit if they cause failures)

- Root-cause selection is purely count-based (largest group = root) except
  for SLURM (priority-ordered reasons). This is right for "one bug breaks
  many tests" but can be wrong for true causal chains (e.g. GCC's *first*
  error causing many *later*, differently-worded errors) — those would
  currently show as separate root/secondary groups rather than being
  linked as cause->effect. No fixture currently exercises that shape.
- `stdout`/`stderr` are merged (`stderr=subprocess.STDOUT`) for `run`, so
  exact interleaving order relies on OS buffering, not a guaranteed
  happens-before relationship.
- Clustering never uses an LLM by design, so semantically-identical errors
  with very different phrasing (not just different numbers/addresses) will
  not collapse together.
