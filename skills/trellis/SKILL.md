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
       S.pde.mesh_convergence(my_solve, resolutions=[10, 20, 40, 80],
                               reference=exact_solution, expected_order=2.0),
       S.universal.nan_inf_check(my_solve(80)),
   ]
   ```
   Each call **executes the check immediately** against your real code —
   there is nothing to "run later"; the numbers in `RESULTS` are already
   the outcome.

2. **Run it**:
   ```
   python3 <this-skill-dir>/scripts/trellis.py run verify_solver.py
   ```
   Exits non-zero on FAIL. Prints every check's status, expected/observed
   values, and *why* (`notes`). Saves the full report (with evidence) to
   `.trellis/<spec>_report.json` — never discarded, always re-inspectable.

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
   numerics.

## Picking checks: what changed → what to run

| What changed | Module.function |
|---|---|
| Any numerical output at all | `universal.nan_inf_check` (always cheap, always worth it) |
| A discretization scheme, stencil, timestep, or mesh resolution | `ode.timestep_convergence` / `pde.mesh_convergence` — **the single highest-value check in this library**; catches order-of-accuracy regressions no fixed-resolution test can see |
| A linear solve, decomposition, or matrix operation | `linalg.residual_norm`, `.conditioning`, `.symmetry_check`, `.positive_definite_check` |
| A "fix" that involved changing a tolerance | Run the *original* tolerance through `linalg.residual_norm`/equivalent yourself — don't trust the changed one (see UC2 in `validate.md`) |
| An ODE integrator | `ode.invariant_preservation` (energy/momentum drift), `ode.reference_solution_comparison` if an analytic solution exists |
| A PDE solver | `pde.conservation_check`, `pde.manufactured_solution_check` if you can construct one |
| An optimizer or its gradient | `optimization.gradient_check` (catches wrong analytic gradients), `.termination_check`, `.constraint_violation_check` |
| A Monte Carlo method, or any result quoted from a single run | `stochastic.seed_replication_check` — **always use >=3 seeds before claiming an improvement**; single-seed numbers are not evidence |
| Comparing a metric before/after a change | `stochastic.compare_before_after` — refuses to call it significant if the CIs overlap |

This table *is* "classify the computation, determine applicable checks" —
there's no automatic classifier; you (the agent) pick based on what
actually changed, the same way you'd pick which tests to run.

## Why numpy

Linear algebra (eigenvalues, condition numbers), convergence-order
fitting, and confidence intervals are well-trodden numerical ground.
Reimplementing them by hand in this library would make the *verification
tool itself* less numerically trustworthy, not more — and any repository
doing the kind of work this skill targets already depends on numpy in
practice. See `references/modules.md` for exactly what's implemented from
scratch (the PASS/WARN/FAIL threshold logic, convergence-order fitting,
CI-overlap comparison) versus what's a thin wrapper over `numpy.linalg`.

## Guarantees

- Every check **executes** against real inputs/callables — nothing here
  is a static recommendation to go verify something yourself.
- Every PASS/WARN/FAIL carries `metric`/`expected`/`observed`/`evidence`
  — machine-readable, not prose.
- WARN and FAIL are distinct and mean different things: WARN = "close to
  the boundary, worth a second look"; FAIL = "this is wrong." Neither is
  silently rounded to the other.
- `Report.unsupported_claims`/`remaining_risks` are partly auto-populated
  from which verification *tiers* (implementation / numerical /
  model_validation / empirical) never ran — see `references/modules.md`.
- Project-specific invariants are just another check: wrap any
  domain-specific rule (a conservation law, an invariant that must never
  be violated) in `universal.parameter_sanity_check` or a bespoke
  `CheckResult` and include it in `RESULTS` like any other.
