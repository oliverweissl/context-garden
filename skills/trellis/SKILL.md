---
name: trellis
description: Numerical correctness gate for scientific code (solvers, finite-difference/element/volume schemes, ODE integrators, optimizers, Monte Carlo, statistics). Use after changing such code, before declaring it correct; passing unit tests is not evidence of convergence order, residual accuracy or statistical validity.
---

# trellis

**Passing tests show software behavior, not numerical correctness.** A
stencil dropping from 2nd to 1st order, a "fix" that relaxed a tolerance,
a Monte Carlo result from one lucky seed: all pass ordinary tests. trellis
is a Python library of checks that execute against your code and return
PASS/WARN/FAIL with evidence. Needs numpy (scipy optional).

1. Write a spec script; each call runs the check immediately:
   ```python
   import sys; sys.path.insert(0, "<this-skill-dir>")
   import trellis as S
   RESULTS = [
       S.pde.mesh_convergence(my_solve, resolutions=[10, 20, 40, 80], param_kind="resolution",
                              reference=exact_solution, expected_order=2.0),
       S.universal.nan_inf_check(my_solve(80)),
   ]
   ```
   `python3 <this-skill-dir>/scripts/trellis.py list-modules` lists all checks.
2. Pin, then run:
   ```
   python3 <this-skill-dir>/scripts/trellis.py lock verify.py   # once; commit .trellis/spec.lock
   python3 <this-skill-dir>/scripts/trellis.py run  verify.py   # exit 1 FAIL, 3 WARN
   ```
3. Read `remaining_risks` and `unsupported_claims`, not just the status.
4. **A FAIL overrides a green test suite.** Fix the numerics. Never relax
   a check, edit `.trellis/spec.lock`, or pass `--update-lock` to make it
   pass; loosening the spec is a human decision. WARN means not verified,
   never report it as correct.

| What changed | Check |
|---|---|
| Any numerical output | `universal.nan_inf_check` |
| Scheme, stencil, timestep, mesh | `ode.timestep_convergence` / `pde.mesh_convergence` (highest value) |
| Linear solve / matrix op | `linalg.residual_norm` (pass `cond=` from `.conditioning`), `.symmetry_check`, `.positive_definite_check` |
| A tolerance was changed | re-check with the *original* tolerance |
| ODE integrator | `ode.invariant_preservation`, `ode.reference_solution_comparison` |
| PDE solver | `pde.conservation_check`, `pde.manufactured_solution_check` |
| Optimizer / gradient | `optimization.gradient_check`, `.termination_check`, `.constraint_violation_check` |
| Monte Carlo / single-run result | `stochastic.seed_replication_check(..., expected=)`, >=3 seeds |
| Before/after metric | `stochastic.compare_before_after` or `run --baseline latest` |
| Sampler / RNG | `stochastic.distribution_sanity_check` |

Result files from C++/Fortran/MPI runs, baselines, lock semantics,
guarantees: `references/guide.md`. Per-module docs: `references/modules.md`.
