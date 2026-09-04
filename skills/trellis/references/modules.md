# Module reference: what's computed, what's a wrapper, what's approximate

Every function executes real computation against the arguments you pass —
none of these are recommendations to go check something yourself. This
page documents exactly what each one computes, what's implemented from
scratch versus delegated to `numpy.linalg`, and the honest limits of the
approximations (mainly in `stochastic.py`).

## `universal` — always applicable

- `nan_inf_check(data)` — `np.isnan`/`np.isinf` over the array. FAIL if
  any found, no WARN tier (there is no "slightly NaN").
- `magnitude_check(value, expected_range)` — plausibility only, a
  sanity net for order-of-magnitude/unit slips, not a correctness proof.
- `reproducibility_check(fn, ...)` — calls `fn` `n_runs` times, compares
  with `np.allclose`. Category `implementation`: this tests the code
  behaves deterministically, not that the deterministic answer is right.
- `parameter_sanity_check(params, constraints)` — arbitrary
  `predicate(value) -> bool` per parameter; this is also how to encode
  **project-specific invariants** ("never let `dt` exceed the CFL limit",
  "tolerance must not be relaxed below X") as an executable check.

## `linalg`

All thin, direct wrappers over `numpy.linalg` — the value added is the
PASS/WARN/FAIL threshold, not the underlying computation:

- `residual_norm(A, x, b, tol)` → `np.linalg.norm(A @ x - b, ord)`
- `conditioning(A)` → `np.linalg.cond(A)`
- `reconstruction_error(original, reconstructed, tol)` → relative
  Frobenius-norm error
- `symmetry_check(A, tol)` → `max(abs(A - A.T))`
- `positive_definite_check(A)` → `np.linalg.eigvalsh` on the symmetrized
  matrix `(A + A.T) / 2`, checks `min(eigenvalues) > 0`. **Symmetrizes
  before checking** — if asymmetry itself is meaningful for your matrix,
  run `symmetry_check` first and interpret accordingly.

## `convergence` (shared by `ode`/`pde`) — the highest-value module

`observed_order(params, errors)`: fits `log(error) = order * log(param) +
const` via `np.polyfit` (ordinary least squares in log-log space) — this
*is* the standard textbook way to read a convergence order off a
refinement study, not an approximation of it.

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

`gradient_check` is a real central-difference computation
(`(f(x+eps) - f(x-eps)) / (2*eps)` per coordinate) compared against your
analytic gradient — this catches a genuinely common and dangerous bug
class (a wrong analytic gradient that still lets the optimizer limp to
*a* result, just not the right one, with no exception or test failure
anywhere). `eps=1e-6` is a reasonable default for double precision;
tighten `rtol` rather than `eps` if you need more sensitivity (`eps` too
small trades truncation error for floating-point cancellation error).

## `stochastic` — the module with real statistical approximations

**No scipy dependency.** Two specific simplifications, both documented in
the functions' own docstrings and repeated here because they matter for
how much to trust a WARN vs. treating it as authoritative:

1. **`confidence_interval_from_samples`** uses the large-sample normal
   approximation to the standard error of the mean
   (`mean ± z * std/sqrt(n)`), not a t-distribution or bootstrap. This
   under-covers slightly for small `n` (rule of thumb: fine for n>=30,
   somewhat too narrow below that — `seed_replication_check` already
   WARNs below n=3 for this reason, but n between 3 and ~10 should be
   read as directionally useful, not a precise 95% guarantee).
2. **`compare_before_after`** decides "distinguishable from noise" by
   whether the two samples' 95% CIs overlap at all. This is a
   conservative heuristic: non-overlapping CIs really does mean the
   difference is very unlikely to be noise, but overlapping CIs does
   *not* strictly rule out a real difference (a proper two-sample test is
   more powerful right at the boundary). Reach for `scipy.stats.ttest_ind`
   directly in your spec script if a borderline call actually matters —
   nothing stops you from importing scipy in your own spec even though
   `trellis` itself doesn't depend on it.

`seed_replication_check` additionally flags the specific bug where every
seed produces a bit-identical result (`std == 0` across >1 seeds) — this
usually means the seed argument isn't actually reaching the RNG, not that
the computation is impressively deterministic. Verified during
development against a deliberately-broken `metric(seed)` that ignores its
argument.
