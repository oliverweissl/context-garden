# Module reference: what's computed, what's a wrapper, what's approximate

Every function executes real computation against the arguments you pass —
none of these are recommendations to go check something yourself. This
page documents exactly what each one computes, what's implemented from
scratch versus delegated to `numpy.linalg`, and the honest limits of the
approximations (mainly in `stochastic.py`).

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
*above* expected, too few samples/seeds, missing `cond` on an
ill-conditioned system, spread-only replication, condition number not
estimable, ARPACK non-convergence, unpinned spec.

## `universal` — always applicable

- `nan_inf_check(data)` — `np.isnan`/`np.isinf` over the array. FAIL if
  any found, no WARN tier (there is no "slightly NaN").
- `magnitude_check(value, expected_range)` — plausibility only, a
  sanity net for order-of-magnitude/unit slips, not a correctness proof.
- `reproducibility_check(fn, ...)` — calls `fn` `n_runs` times, compares
  with `np.allclose(..., equal_nan=True)`; NaN in the output downgrades a
  PASS to WARN (reproducible NaN is still a breakdown). Category `implementation`: this tests the code
  behaves deterministically, not that the deterministic answer is right.
- `threshold_check(value_or_path, tol, key=None)` — strict `value <= tol`
  for a scalar the solver already computed (e.g. a residual in a JSON file
  written by an MPI job).
- `parameter_sanity_check(params, constraints)` — arbitrary
  `predicate(value) -> bool` per parameter; this is also how to encode
  **project-specific invariants** ("never let `dt` exceed the CFL limit",
  "tolerance must not be relaxed below X") as an executable check.

## `linalg`

Dense input: thin wrappers over `numpy.linalg` — the value added is the
PASS/WARN/FAIL threshold, not the underlying computation.
Sparse (`scipy.sparse`) / `LinearOperator` (or any object with `.matvec`,
optional `.rmatvec`, `.shape`) input is never densified; scipy is imported
lazily only for such input, and a missing scipy gives a clear ImportError
(a duck-typed operator with matvec/rmatvec works numpy-only for
`residual_norm` with `ord=2`).

- `residual_norm(A, x, b, tol, relative=True, cond=None)` → normwise
  backward error `||Ax-b|| / (||A||·||x|| + ||b||)` (absolute residual also
  reported); with `cond`, WARNs if `cond · backward_error > tol`. `||A||`:
  exact for dense and for sparse with ord 1/inf/fro
  (`scipy.sparse.linalg.norm`); ord 2 for sparse/operators by power
  iteration on AᴴA (or random probes without `rmatvec`); ord 1/inf for
  operators via `onenormest`. All estimates are lower bounds of `||A||`,
  which can only *raise* the relative residual (conservative).
  `metric.matrix_norm_method` says which was used.
- `conditioning(A, inverse=None)` → dense: `np.linalg.cond` (2-norm).
  Sparse: `onenormest(A) · onenormest(A⁻¹)` with A⁻¹ applied via
  `scipy.sparse.linalg.splu` (1-norm estimate, typically within ~3× and
  a lower bound, as LAPACK's `*gecon`); a singular LU → FAIL; LU out of
  memory → WARN. LinearOperator: needs `inverse=` (operator with rmatvec,
  or a solve callable) else WARN "not estimated".
- `reconstruction_error(original, reconstructed, tol)` → relative
  Frobenius-norm error (sparse allowed)
- `symmetry_check(A, tol)` → `max(abs(A - A.T))` (sparse: on the sparse
  difference; operator: random-probe lower bound)
- `positive_definite_check(A)` → `np.linalg.eigvalsh` on the symmetrized
  matrix `(A + A.T) / 2`, checks `min(eigenvalues) > n·eps·max|eig|`;
  sparse/operators with n > 64 use `eigsh` for the extreme eigenvalues
  (non-convergence → WARN). **Symmetrizes before checking** — if
  asymmetry itself is meaningful for your matrix, run `symmetry_check`
  first and interpret accordingly.

## `convergence` (shared by `ode`/`pde`) — the highest-value module

`observed_order(params, errors)`: fits `log(error) = order * log(h) +
const` via `np.polyfit` (reported as `fit_order`). The verdict uses
per-pair `local_orders` and judges the **finest usable pair**.

Parameters are step sizes `h` (`param_kind="h"`) or cell counts `N`
(`param_kind="resolution"`, fitted against `h = 1/N`); `mesh_convergence`
defaults to `"auto"` (integers >= 2 → cell counts). The default error is
the grid-scaled RMS norm `||e||/sqrt(size)` — an unscaled Euclidean norm
would lower the observed order by d/2 in d dimensions. With
`reference=None` the order comes from successive differences
`|u_h - u_{h/2}|` (Richardson-style, exact for a constant refinement
ratio); outputs of different shapes need an `error_fn` that restricts
them to common points. Guards: levels at a round-off/solver-tolerance
floor are trimmed (WARN), local orders that have not stabilised or drift
away from the expected order give WARN (pre-asymptotic), and an order
*higher* than expected is WARN, not PASS.

`convergence_check(solve_fn, param_values, reference, expected_order,
tol_order)`: actually calls `solve_fn` once per value in `param_values`
(so this executes your real solver at multiple resolutions/timesteps —
it is not free, budget for it) and fits the order from the resulting
errors. Verified against known finite-difference theory during
development (`tests/smoke_test.sh`'s library-level check): a forward
difference fits to order ≈1.01, a central difference to order ≈2.00,
matching textbook 1st/2nd-order accuracy exactly. **This is the check
that catches UC1** — a stencil bug that silently drops a scheme's order
still produces a "reasonable-looking" answer at any single fixed
resolution (which is why ordinary tests miss it), but the *slope* of
error vs. resolution is unambiguous.

`tol_order` (default 0.3) is a fit-noise allowance, not a physical
tolerance — 0.3 comfortably separates a 1st-order scheme (order≈1) from a
2nd-order one (order≈2) while tolerating realistic numerical noise in the
fit; tighten it for schemes with closer expected orders (e.g.
distinguishing 3rd from 4th order) and expect to need more/better-spaced
resolution points as you do.

### Results from files (`trellis.io`)

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

`timestep_convergence`/`mesh_convergence` are direct calls into
`convergence_check` (see above) with domain-appropriate parameter naming.
`invariant_preservation`/`conservation_check` are the same relative-drift
computation under two names (energy/momentum for ODEs, mass/energy for
PDEs — same math, different physical intent, kept as separate named
functions for spec-script readability rather than one generic
"drift_check"). `reference_solution_comparison`/`manufactured_solution_check`
are tagged `model_validation`, not `numerical` — they validate against a
*known-correct* answer, which is a stronger and different claim than
internal self-consistency (see `schema.md`'s category definitions).

## `optimization`

`gradient_check` compares a central difference `D(h) = (f(x+h) -
f(x-h)) / 2h` per coordinate against your analytic gradient — this
catches a genuinely common and dangerous bug class (a wrong analytic
gradient that still lets the optimizer limp to *a* result). Step
`h_i = eps·max(1, |x_i|)`, default `eps = eps_mach^(1/3)` (≈6e-6, the
step balancing truncation O(h²) against round-off O(eps/h)).

The error of the FD derivative itself is estimated per coordinate:

- truncation: Richardson — `D(h) = g + c·h² + …`, so
  `|D(2h) − D(h)| / 3 ≈ |c|·h²` (costs two extra evaluations);
- round-off: each f evaluation is accurate to `f_rtol·|f|` (default
  `100·eps_mach`; raise it for noisy objectives), giving
  `f_rtol·(|f(x+h)| + |f(x−h)|) / 2h`.

With `fd_err_i` their sum, the relative error uses the scale-aware
denominator `max(|g_an|, |g_fd|, fd_safety·fd_err_i / rtol)`
(`fd_safety = 10`), i.e. PASS iff
`|g_an − g_fd| ≤ max(rtol·|g|, fd_safety·fd_err_i)`. Away from stationary
points this is the plain relative test (a 0.5% error FAILs at
`rtol = 1e-3`); at/near a stationary point, or when `|f| ≫ |g|·h` makes
the difference cancellation-dominated, the comparison falls back to what
central differences can actually resolve instead of dividing by ≈0, so
correct gradients no longer false-FAIL (a *wrong* nonzero gradient at a
stationary point still FAILs). Coordinates judged on that floor are
listed in `evidence.floor_limited_coordinates`.

## `stochastic` — the module with real statistical approximations

**No scipy dependency.** Three specific simplifications, all documented in
the functions' own docstrings and repeated here because they matter for
how much to trust a WARN vs. treating it as authoritative:

1. **`confidence_interval_from_samples`** is a Student-t interval
   (`mean ± t * std/sqrt(n)`), t quantiles from a table (df <= 30) or a
   Cornish-Fisher expansion (df > 30). Confidence must be 0.90, 0.95 or
   0.99 (anything else raises `ValueError`); n < 2 gives `[None, None]`.
   It assumes roughly normal seed-to-seed variation.
2. **`compare_before_after(before, after, direction=None)`** is a Welch
   t-test (non-integer df rounded down). Fewer than 2 samples per side →
   WARN; not significant → WARN; significant → PASS, or FAIL if
   `direction` says it is a regression. Reach for `scipy.stats` directly
   in your spec script if you need exact p-values.

3. **`distribution_sanity_check(values, expected_mean, expected_std,
   reference, alpha=0.01, assume_normal=False)`** — up to three tests,
   Bonferroni-corrected over the tests actually run (each at `alpha/m`):
   - mean: one-sample t-test `t = (x̄ − μ)/(s/√n)`, exact two-sided
     p-value via the regularized incomplete beta (so the tolerance shrinks
     with n: bias 0.15σ at n = 100k is t ≈ 47 → FAIL);
   - variance: chi-square test on `k·s²/σ²` with Wilson–Hilferty
     p-values. `k = n−1` under normality (`assume_normal=True`); by
     default `k` is kurtosis-adjusted (`Var(s²/σ²) ≈ 2/(n−1) + κ/n`,
     `k = 2/Var`), because the classical test is not robust: for a correct
     exponential sampler it false-FAILs ~20% of seeds, the adjusted test
     ~1%;
   - shape: KS vs a CDF callable (one-sample) or a reference sample
     (two-sample), asymptotic Kolmogorov p-value with Stephens'
     correction; assumes continuous data (ties → conservative).
   Measured false-FAIL rate for a correct N(0,1) sampler (n = 10k, all
   three tests, 200 seeds): 1/200; exponential (mean+variance): 2/200.
   FAIL if any p < alpha/m; WARN for n < 2 or when nothing was tested.
   `normal_cdf` is provided as a numpy-only reference CDF.

`seed_replication_check(fn, seeds, expected=None, atol=0, rtol=0)`: any
NaN/inf → FAIL; with `expected`, FAIL if it lies outside the CI (widened
by `atol + rtol·|expected|`), i.e. a detectable bias; without `expected`
it is WARN ("replicated, spread only") since spread says nothing about
correctness.

`seed_replication_check` additionally flags the specific bug where every
seed produces a bit-identical result (`std == 0` across >1 seeds) — this
usually means the seed argument isn't actually reaching the RNG, not that
the computation is impressively deterministic. Verified during
development against a deliberately-broken `metric(seed)` that ignores its
argument.
