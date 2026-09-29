# Module reference: per-check options and limits

## Strictness rule (all modules)

`_util.threshold_status(value, tol)`: `value > tol` (or NaN) is **FAIL**,
else PASS — no slack band. Used by `residual_norm`,
`reconstruction_error`, `symmetry_check`, `reference_solution_comparison`,
`invariant_preservation`, `conservation_check`,
`constraint_violation_check`, `gradient_check`,
`initialization_sensitivity_check`, `threshold_check`. Likewise
`magnitude_check` FAILs outside its range and a stabilised convergence
order outside `expected ± tol_order` FAILs. WARN is only produced for
indeterminate outcomes: pre-asymptotic orders, round-off floors, an order
*above* expected, too few samples/seeds, `cond · backward_error > tol`
on a small residual, spread-only replication, condition number not
estimable, ARPACK non-convergence, unpinned spec.

## `universal` — always applicable

- `nan_inf_check(data)` — FAIL if any NaN/Inf; no WARN tier.
- `magnitude_check(value, expected_range)` — plausibility net for
  order-of-magnitude/unit slips, not a correctness proof.
- `reproducibility_check(fn, ...)` — `n_runs` calls compared with
  `np.allclose(..., equal_nan=True)`; NaN in the output downgrades PASS to
  WARN. Category `implementation`: deterministic, not necessarily right.
- `threshold_check(value_or_path, tol, key=None)` — strict `value <= tol`
  for a scalar the solver already computed (e.g. in a JSON file from an
  MPI job).
- `parameter_sanity_check(params, constraints)` — `predicate(value) -> bool`
  per parameter; use it for **project-specific invariants** ("`dt` never
  exceeds the CFL limit"). Anything else can be a bespoke `CheckResult`
  (`trellis.CheckResult`) appended to `RESULTS`.

## `linalg`

Sparse (`scipy.sparse`) and `LinearOperator` input (any object with
`.matvec`, optional `.rmatvec`, `.shape`) is never densified; scipy is
imported lazily only for such input (a duck-typed operator works
numpy-only for `residual_norm` with `ord=2`).

- `residual_norm(A, x, b, tol, relative=True, cond=None)` → normwise
  backward error `||Ax-b|| / (||A||·||x|| + ||b||)`. Without `cond` a PASS
  certifies only the backward error; with `cond` (from `conditioning`) it
  WARNs if `cond · backward_error > tol` (forward error not bounded).
  `||A||` estimates for sparse/operators are lower bounds (conservative);
  see `metric.matrix_norm_method`.
- `conditioning(A, inverse=None)` → dense: 2-norm cond. Sparse: 1-norm
  estimate via `splu` (lower bound); singular LU → FAIL, LU out of memory →
  WARN. LinearOperator: needs `inverse=` (operator with rmatvec, or a
  solve callable), else WARN "not estimated".
- `reconstruction_error(original, reconstructed, tol)` → relative
  Frobenius error.
- `symmetry_check(A, tol)` → `max(abs(A - A.T))` (operator: random-probe
  lower bound).
- `positive_definite_check(A)` → `min(eig) > n·eps·max|eig|` on the
  **symmetrized** `(A + A.T) / 2`; run `symmetry_check` first if asymmetry
  matters. Large sparse/operators use `eigsh` (non-convergence → WARN).

## `convergence` (shared by `ode`/`pde`) — the highest-value module

`observed_order(params, errors)` reports a least-squares `fit_order`, but
the verdict uses per-pair `local_orders` and judges the **finest usable
pair**.

Parameters are step sizes `h` (`param_kind="h"`) or cell counts `N`
(`param_kind="resolution"`, fitted against `h = 1/N`); `mesh_convergence`
defaults to `"auto"` (integers >= 2 → cell counts). The default error is
the grid-scaled RMS norm `||e||/sqrt(size)` — an unscaled Euclidean norm
would lower the observed order by d/2 in d dimensions. With
`reference=None` the order comes from successive differences
`|u_h - u_{h/2}|` (exact for a constant refinement ratio); outputs of
different shapes need an `error_fn` that restricts them to common points.
Guards: levels at a round-off/solver-tolerance floor are trimmed (WARN),
local orders that have not stabilised or drift away from the expected
order give WARN (pre-asymptotic), and an order *higher* than expected is
WARN, not PASS.

`convergence_check(solve_fn, param_values, reference, expected_order,
tol_order)` calls your real solver once per value — budget for it.

`tol_order` (default 0.3) is fit-noise allowance; tighten it (with more
resolutions) to separate close orders such as 3 vs 4.

### Results from files (`trellis.io`)

Checks don't need a Python callable; pass result files produced by
C++/Fortran/MPI/SLURM jobs:
`mesh_convergence("out/n{N}.npy", [16, 32, 64], reference="out/exact{N}.npy", expected_order=2)`,
`mesh_convergence(None, errors="out/errors.json", error_key="error", expected_order=2)`,
`universal.threshold_check("out/run.json", 1e-8, key="stats.residual")`.

Convergence checks accept `solve_fn` / `reference` as a file template
(`"out/n{N}.npy"`, formatted with `N`/`n`/`h`/`dt`/`p`) or, for the
reference, one fixed file; `file_key` / `reference_key` pick the npz array,
CSV column (name or index) or JSON key path. `errors=` takes precomputed
errors per resolution (dict, or a JSON/CSV path; see `io.load_errors` for
the accepted layouts: `{"8": 0.01}`, `{"8": {"error": ...}}`,
`[{"N": 8, "error": ...}]`, `{"N": [...], "errors": [...]}`, 2-column
CSV) with `solve_fn=None`; `solution_scale=` then sets the round-off-floor
scale. `io.load(path, key)` handles `.npy`, `.npz`, `.json` and any text
table (`np.loadtxt`; delimiter `,`/`;`/tab/whitespace and a header row are
auto-detected).

## `ode` / `pde`

`timestep_convergence`/`mesh_convergence` call `convergence_check`.
`invariant_preservation`/`conservation_check` are the same relative-drift
check under two names. `reference_solution_comparison`/
`manufactured_solution_check` are tagged `model_validation`, not
`numerical`: they compare against a known-correct answer.

## `optimization`

`gradient_check` compares a central difference per coordinate against
your analytic gradient; its tolerance accounts for the FD error itself.
Raise `f_rtol` (default `100·eps_mach`) for noisy objectives (e.g. an
iterative inner solve). Coordinates whose `|g|` is below what central
differences resolve are judged on that floor and listed in
`evidence.floor_limited_coordinates`, so correct gradients don't
false-FAIL at a stationary point; a *wrong* nonzero gradient there still
FAILs.

## `stochastic` — no scipy

- `confidence_interval_from_samples` — Student-t interval; confidence must
  be 0.90, 0.95 or 0.99 (else `ValueError`); n < 2 gives `[None, None]`.
  Assumes roughly normal seed-to-seed variation.
- `compare_before_after(before, after, direction=None)` — Welch t-test.
  Fewer than 2 samples per side → WARN; not significant → WARN;
  significant → PASS, or FAIL if `direction` says it is a regression. Use
  `scipy.stats` in the spec if you need exact p-values.
- `distribution_sanity_check(values, expected_mean, expected_std,
  reference, alpha=0.01, assume_normal=False)` — mean t-test, variance
  chi-square, and KS vs a CDF callable or reference sample, Bonferroni
  over the tests run. The mean test tightens with n (bias 0.15σ at
  n = 100k FAILs). The variance test is kurtosis-adjusted by default
  (the classical one false-FAILs ~20% of seeds for a correct exponential
  sampler); `assume_normal=True` uses classical `n−1`. KS assumes
  continuous data (ties → conservative). Measured false-FAIL rate, 200
  seeds, n = 10k: N(0,1) all three tests 1/200; exponential
  (mean+variance) 2/200. FAIL if any p < alpha/m; WARN for n < 2 or
  nothing tested. `normal_cdf` is a numpy-only reference CDF.
- `seed_replication_check(fn, seeds, expected=None, atol=0, rtol=0)` —
  NaN/inf → FAIL; with `expected`, FAIL if it lies outside the CI (widened
  by `atol + rtol·|expected|`); without `expected` only WARN ("spread
  only"). Bit-identical results across >1 seeds are flagged: the seed
  usually isn't reaching the RNG.
