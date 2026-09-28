---
name: trellis
description: Independent scientific/numerical correctness gate — use after any change to numerical or scientific code (solvers, finite-difference/element/volume schemes, optimizers, Monte Carlo methods, statistics) before declaring it correct. Passing unit tests or "it runs without error" is NOT sufficient evidence of numerical correctness; this executes deterministic checks (convergence order, residuals, conditioning, replication) that catch regressions ordinary tests miss.
---

# trellis

**The rule this skill exists to enforce: passing unit tests is evidence of
software *behavior*, not of numerical/scientific *correctness*.** A
finite-difference stencil bug that drops a scheme from 2nd-order to
1st-order accuracy, a solver "fix" that relaxed a tolerance instead of
fixing a residual, a Monte Carlo "improvement" measured on one lucky seed
— all of these pass ordinary tests. None of them are correct. This skill
is a modular, importable Python library (`trellis/`) of checks that
*execute* against your actual numerical code and produce PASS/WARN/FAIL
with machine-readable evidence — not a checklist to eyeball.

Requires **numpy** in whatever Python runs it (`pip install numpy` if
missing) — the one non-stdlib dependency in this skill suite; see
"Why numpy" below.

## Workflow

1. **After any change to numerical/scientific code**, before claiming it
   works: pick the check functions that apply (see the table below, or
   run `python3 <this-skill-dir>/scripts/trellis.py list-modules` for
   the full list with one-line docs), and write a small spec script:

   ```python
   # verify_solver.py
   import sys; sys.path.insert(0, "<this-skill-dir>")
   import numpy as np
   import trellis as S

   RESULTS = [
       # my_solve(N) / exact_solution(N): N = cell count; order is fitted vs h = 1/N
       S.pde.mesh_convergence(my_solve, resolutions=[10, 20, 40, 80], param_kind="resolution",
                               reference=exact_solution, expected_order=2.0),
       S.universal.nan_inf_check(my_solve(80)),
   ]
   ```
   Each call **executes the check immediately** against your real code —
   there is nothing to "run later"; the numbers in `RESULTS` are already
   the outcome. Convergence checks use the grid-scaled RMS error by
   default (dimension-independent), judge the finest pair's local order,
   and WARN (not PASS/FAIL) on round-off floors or unstabilised orders.
   Pass `param_kind="h"` when you pass step sizes instead of cell counts.

2. **Pin it, then run it**:
   ```
   python3 <this-skill-dir>/scripts/trellis.py lock verify_solver.py   # once; commit .trellis/spec.lock
   python3 <this-skill-dir>/scripts/trellis.py run  verify_solver.py
   ```
   Exits 1 on FAIL and 3 on WARN (`--allow-warn` makes WARN exit 0).
   Prints every check's status, expected/observed
   values, and *why* (`notes`). Saves the full report (with evidence) to
   `.trellis/<spec>_report.json` — never discarded, always re-inspectable.

   **Spec lock.** `trellis lock` records every check's strictness (expected
   orders, `tol`/`tol_order`/`rtol`, `alpha`, resolutions, seeds, reference
   fingerprints) in `<git root>/.trellis/spec.lock`. `trellis run` compares
   the spec against it: anything *looser* (lower expected order, larger
   tolerance, smaller alpha, removed check, changed reference/resolutions)
   is a FAIL with a diff; tighter changes pass and are reported; no lock
   entry is WARN ("unpinned spec"). **Never run `--update-lock` to make a
   failing gate pass** — accepting a looser spec is a human decision, made
   by reviewing the lock's `git diff`. If the lock FAILs, restore the
   original strictness and fix the numerics.

   **Baselines (optional).** `run --save-baseline` stores each check's
   metrics under `.trellis/baselines/<git-sha>.json`; `run --baseline
   <sha|latest>` FAILs on regression vs that commit (observed order dropped
   by > `tol_order`, an error/residual metric grew > `--regression-factor`,
   default 2×). Commit `.trellis/spec.lock`; baselines are optional to
   commit; ignore `*_report.json` (e.g. `.trellis/*` + `!.trellis/spec.lock`
   + optionally `!.trellis/baselines/`).

   **Real solvers.** Checks don't need a Python callable: pass result files
   produced by C++/Fortran/MPI/SLURM jobs — `mesh_convergence("out/n{N}.npy",
   [16, 32, 64], reference="out/exact{N}.npy", expected_order=2)` (npy/npz/
   CSV/text/JSON; `file_key=` picks an npz array, CSV column or JSON key
   path), `mesh_convergence(None, errors="out/errors.json", error_key="error",
   expected_order=2)` for precomputed errors per resolution, and
   `universal.threshold_check("out/run.json", 1e-8, key="stats.residual")`
   for a reported scalar. `linalg.*` accepts scipy.sparse matrices and
   LinearOperators without densifying (scipy optional; see
   `references/modules.md`).

3. **Read `remaining_risks` and `unsupported_claims` before declaring
   success**, not just the top-line status. These are partly automatic:
   if your spec never ran a `model_validation`-tier check (comparison
   against a known-correct reference solution) or an `empirical`-tier
   check (multi-seed replication), the report says so *even if every
   check that did run passed* — a clean PASS on residuals alone does not
   mean the underlying model is right, and this skill will not let that
   go unremarked.

4. **A FAIL here overrides a passing test suite.** If `pytest`/`ctest`
   are green but trellis FAILs, the numerical work is not done — don't
   report success, don't relax the check to make it pass (that's exactly
   the UC2 failure mode this skill exists to catch), fix the actual
   numerics. This includes the lock: do not edit `.trellis/spec.lock` or
   pass `--update-lock` yourself — if a looser spec is genuinely right,
   say so and leave the `--update-lock` + diff review to the human.

## Picking checks: what changed → what to run

| What changed | Module.function |
|---|---|
| Any numerical output at all | `universal.nan_inf_check` (always cheap, always worth it) |
| A discretization scheme, stencil, timestep, or mesh resolution | `ode.timestep_convergence` / `pde.mesh_convergence` — **the single highest-value check in this library**; catches order-of-accuracy regressions no fixed-resolution test can see |
| A linear solve, decomposition, or matrix operation | `linalg.residual_norm`, `.conditioning`, `.symmetry_check`, `.positive_definite_check` — pass `cond=` (from `conditioning`) to `residual_norm` for ill-conditioned systems: a small residual (backward error) does not imply a small forward error in x |
| A "fix" that involved changing a tolerance | Run the *original* tolerance through `linalg.residual_norm`/equivalent yourself — don't trust the changed one (see UC2 in `tests/smoke_test.sh`) |
| An ODE integrator | `ode.invariant_preservation` (energy/momentum drift), `ode.reference_solution_comparison` if an analytic solution exists |
| A PDE solver | `pde.conservation_check`, `pde.manufactured_solution_check` if you can construct one |
| An optimizer or its gradient | `optimization.gradient_check` (catches wrong analytic gradients), `.termination_check`, `.constraint_violation_check` |
| A Monte Carlo method, or any result quoted from a single run | `stochastic.seed_replication_check(..., expected=<true value>)` — **always use >=3 seeds**; without `expected` it can only WARN (spread measured, correctness not) |
| Comparing a metric before/after a change | `stochastic.compare_before_after(before, after, direction="higher_is_better"/"lower_is_better")` — Welch t-test; needs >=2 samples per side, FAILs on a significant regression; or `trellis run --baseline latest` across commits |
| A sampler / random-number generator | `stochastic.distribution_sanity_check(values, expected_mean=, expected_std=, reference=cdf_or_sample)` — t-test, chi-square and KS with Bonferroni at `alpha=0.01` |
| Results only exist as files from an external (C++/Fortran/MPI) run | the same checks with file paths / templates, `errors=` JSON, or `universal.threshold_check(path, tol, key=)` — see `trellis.io` |

This table *is* "classify the computation, determine applicable checks" —
there's no automatic classifier; you (the agent) pick based on what
actually changed, the same way you'd pick which tests to run.

## Why numpy (scipy optional)

See `references/why-numpy.md`.

## Guarantees

- Every check **executes** against real inputs/callables — nothing here
  is a static recommendation to go verify something yourself.
- Every PASS/WARN/FAIL carries `metric`/`expected`/`observed`/`evidence`
  — machine-readable, not prose.
- WARN and FAIL are distinct and mean different things: FAIL = a value
  exceeded its tolerance (strict — no slack band; `value > tol` is FAIL)
  or a test rejected; WARN = **indeterminate / not verified**
  (pre-asymptotic, round-off floor, too few samples, missing `cond`,
  spread-only replication, unpinned spec) — never report a WARN as
  "correct". Neither is silently rounded to the other, and WARN exits
  non-zero by default.
- The strictness of a pinned spec can only be relaxed through a reviewed
  change to the committed `.trellis/spec.lock` — never by the agent.
- `Report.unsupported_claims`/`remaining_risks` are partly auto-populated
  from which verification *tiers* (implementation / numerical /
  model_validation / empirical) never ran — see `references/modules.md`.
- Project-specific invariants are just another check: wrap any
  domain-specific rule (a conservation law, an invariant that must never
  be violated) in `universal.parameter_sanity_check` or a bespoke
  `CheckResult` and include it in `RESULTS` like any other.
