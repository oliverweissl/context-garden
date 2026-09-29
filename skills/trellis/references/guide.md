# trellis: operational details

Moved out of `SKILL.md` to keep the on-trigger body small. Why numpy:
`why-numpy.md`.

## Convergence checks

Convergence checks use the grid-scaled RMS error by default
(dimension-independent), judge the finest pair's local order, and WARN
(not PASS/FAIL) on round-off floors or unstabilised orders. Pass
`param_kind="h"` when you pass step sizes instead of cell counts.

## Run output

`run` prints every check's status, expected/observed values, and *why*
(`notes`), and saves the full report (with evidence) to
`.trellis/<spec>_report.json`. `--allow-warn` makes WARN exit 0.

## Spec lock

`trellis lock` records every check's strictness (expected orders,
`tol`/`tol_order`/`rtol`, `alpha`, resolutions, seeds, reference
fingerprints) in `<git root>/.trellis/spec.lock`. `trellis run` compares
the spec against it: anything *looser* (lower expected order, larger
tolerance, smaller alpha, removed check, changed reference/resolutions) is
a FAIL with a diff; tighter changes pass and are reported; no lock entry
is WARN ("unpinned spec"). **Never run `--update-lock` to make a failing
gate pass** -- accepting a looser spec is a human decision, made by
reviewing the lock's `git diff`. If the lock FAILs, restore the original
strictness and fix the numerics. If a looser spec is genuinely right, say
so and leave the `--update-lock` + diff review to the human.

## Baselines (optional)

`run --save-baseline` stores each check's metrics under
`.trellis/baselines/<git-sha>.json`; `run --baseline <sha|latest>` FAILs on
regression vs that commit (observed order dropped by > `tol_order`, an
error/residual metric grew > `--regression-factor`, default 2x). Commit
`.trellis/spec.lock`; baselines are optional to commit; ignore
`*_report.json` (e.g. `.trellis/*` + `!.trellis/spec.lock` + optionally
`!.trellis/baselines/`).

## Real solvers (result files)

Checks don't need a Python callable: pass result files produced by
C++/Fortran/MPI/SLURM jobs -- `mesh_convergence("out/n{N}.npy", [16, 32, 64],
reference="out/exact{N}.npy", expected_order=2)` (npy/npz/CSV/text/JSON;
`file_key=` picks an npz array, CSV column or JSON key path),
`mesh_convergence(None, errors="out/errors.json", error_key="error",
expected_order=2)` for precomputed errors per resolution, and
`universal.threshold_check("out/run.json", 1e-8, key="stats.residual")` for
a reported scalar. `linalg.*` accepts scipy.sparse matrices and
LinearOperators without densifying.

## Check notes

- `linalg.residual_norm`: a small residual (backward error) does not imply
  a small forward error in x for ill-conditioned systems; pass `cond=`.
- Tolerance changes: run the *original* tolerance through
  `linalg.residual_norm`/equivalent yourself -- don't trust the changed one
  (see UC2 in `tests/smoke_test.sh`).
- `stochastic.seed_replication_check`: **always use >=3 seeds**; without
  `expected` it can only WARN (spread measured, correctness not).
- `stochastic.compare_before_after(before, after,
  direction="higher_is_better"/"lower_is_better")`: Welch t-test; needs >=2
  samples per side, FAILs on a significant regression.
- `stochastic.distribution_sanity_check(values, expected_mean=,
  expected_std=, reference=cdf_or_sample)`: t-test, chi-square and KS with
  Bonferroni at `alpha=0.01`.
- There's no automatic classifier; you pick checks based on what actually
  changed, the same way you'd pick which tests to run.

## Guarantees

- Every check **executes** against real inputs/callables -- nothing here
  is a static recommendation to go verify something yourself.
- Every PASS/WARN/FAIL carries `metric`/`expected`/`observed`/`evidence`
  -- machine-readable, not prose.
- WARN and FAIL are distinct: FAIL = a value exceeded its tolerance
  (strict -- no slack band; `value > tol` is FAIL) or a test rejected;
  WARN = **indeterminate / not verified** (pre-asymptotic, round-off floor,
  too few samples, missing `cond`, spread-only replication, unpinned spec)
  -- never report a WARN as "correct". Neither is silently rounded to the
  other, and WARN exits non-zero by default.
- The strictness of a pinned spec can only be relaxed through a reviewed
  change to the committed `.trellis/spec.lock` -- never by the agent.
- `Report.unsupported_claims`/`remaining_risks` are partly auto-populated
  from which verification *tiers* (implementation / numerical /
  model_validation / empirical) never ran: a clean PASS on residuals alone
  does not mean the underlying model is right.
- Project-specific invariants are just another check: wrap any
  domain-specific rule in `universal.parameter_sanity_check` or a bespoke
  `CheckResult` and include it in `RESULTS`.

## Rules, verbatim

- The full report (with evidence) is saved to `.trellis/<spec>_report.json`
  -- never discarded, always re-inspectable.
- `remaining_risks`/`unsupported_claims` are partly automatic: if your spec
  never ran a `model_validation`-tier check (comparison against a
  known-correct reference solution) or an `empirical`-tier check
  (multi-seed replication), the report says so *even if every check that
  did run passed* -- a clean PASS on residuals alone does not mean the
  underlying model is right, and this skill will not let that go unremarked.
- **A FAIL here overrides a passing test suite.** If `pytest`/`ctest` are
  green but trellis FAILs, the numerical work is not done -- don't report
  success, don't relax the check to make it pass (that's exactly the UC2
  failure mode this skill exists to catch), fix the actual numerics.
- This includes the lock: do not edit `.trellis/spec.lock` or pass
  `--update-lock` yourself -- if a looser spec is genuinely right, say so and
  leave the `--update-lock` + diff review to the human.
- Any numerical output at all: `universal.nan_inf_check` (always cheap,
  always worth it).
- A Monte Carlo method, or any result quoted from a single run:
  `stochastic.seed_replication_check(..., expected=<true value>)` --
  **always use >=3 seeds**; without `expected` it can only WARN (spread
  measured, correctness not).
- Project-specific invariants are just another check: wrap any
  domain-specific rule (a conservation law, an invariant that must never be
  violated) in `universal.parameter_sanity_check` or a bespoke `CheckResult`
  and include it in `RESULTS` like any other.
