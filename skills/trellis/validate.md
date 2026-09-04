# Validating trellis

trellis's job is: catch numerical/scientific defects that pass
ordinary tests, with executable evidence, and never let a report imply
more scientific confidence than what actually ran supports. Validation
checks four things: **check correctness** (does each function compute
what it claims, verified against hand-computable ground truth — this
matters more here than in the other three skills, since a wrong check is
worse than no check), **defect detection** (does it actually catch the
three worked UC scenarios where ordinary tests don't), **honest reporting**
(WARN vs FAIL distinct, gaps auto-flagged, nothing overclaimed), and
**evidence preservation** (every report is saved, re-inspectable, exit
codes composable).

## 0. Requires numpy

```
python3 -c "import numpy" || pip install numpy
```
`tests/smoke_test.sh` checks for numpy in `$PYTHON_BIN` (default
`python3`) and prints `SKIPPED` (exit 0) rather than failing if it's
missing — this sandbox's default `python3` does not have numpy installed;
development and verification here were done against an isolated venv
(`python3 -m venv`, `pip install numpy`) to avoid touching any system
Python. Point `PYTHON_BIN` at an interpreter with numpy to actually run
the suite:
```
PYTHON_BIN=/path/to/venv/bin/python3 bash tests/smoke_test.sh
```

## 1. Automated checks

```
PYTHON_BIN=<python-with-numpy> bash tests/smoke_test.sh
```

Expected: `ALL CHECKS PASSED`, 18/18 checks. It covers two things
end-to-end rather than testing the library in a vacuum:

**Library-level checks against hand-computable ground truth** (this is
the most important part of this suite specifically — a verification
tool's own numerics have to be right, so every non-trivial formula is
checked against a known textbook answer, not just against itself):
- forward-difference numerical derivative fits to observed order ≈1.01,
  central-difference to ≈2.00 — exactly matching classical 1st/2nd-order
  finite-difference accuracy theory, not just "some number came out".
- the same forward-difference scheme, judged against `expected_order=2.0`
  (i.e. treated as if it were meant to be the 2nd-order central-difference
  scheme, which is the actual UC1 bug shape) correctly FAILs.
- `positive_definite_check`/`symmetry_check` against matrices with known,
  hand-computed eigenvalues/asymmetry.
- a 2000-sample draw from a known `Normal(50, 3)` produces a 95% CI that
  actually brackets 50.0.
- NaN input always FAILs (never WARNs — there's no such thing as a little
  bit NaN).

**UC1/UC2/UC3 end-to-end via the CLI** against
`tests/fixtures/uc{1,2,3}_*.py`: correct overall status, correct exit
code, the specific failing check named and explained in `notes`, the
saved JSON report's status matching the CLI's printed status, and the
auto-populated `remaining_risks`/`unsupported_claims` actually showing up
for tiers that weren't exercised.

## 2. Concrete defect-detection numbers (UC1)

The spec's success criterion is "substantially higher defect detection
than ordinary unit tests" — measured directly rather than assumed, using
the UC1 fixture (a scheme accidentally regressed from 2nd-order
central-difference to 1st-order forward-difference, `d/dx sin(x)` at
`x=0.7`, true value `cos(0.7)`):

| test resolution | regressed-scheme error | passes a typical `tol=1e-2` unit test? |
|---|---|---|
| h=0.1 | 0.0335 | fails (accidentally still catches the bug — but only because the test happens to sit at a coarse resolution where the missing order actually shows up numerically) |
| h=0.01 | 0.0032 | **passes** |
| h=0.001 | 0.0003 | **passes** |

At any test resolution finer than a coarse ~0.05, a fixed-resolution unit
test with a realistic tolerance **passes the regressed scheme** — the
absolute error is still small, just not shrinking at the rate it should.
`pde.mesh_convergence` catches it at every resolution tested, because it
checks the *slope* of error vs. resolution, not a single point. This is
the concrete mechanism behind the spec's UC1 example ("Normal tests pass.
Trellis finds convergence order changed from ~2 to ~1").

Reproduce: `tests/smoke_test.sh`'s library-level check, or run
`SENTINEL_UC1_BUGGED=1 python3 scripts/trellis.py run tests/fixtures/uc1_pde_stencil_regression.py`.

## 3. Fidelity checks against the spec's acceptance criteria

| Criterion (from spec) | How to check | Status |
|---|---|---|
| Initial modules: generic numerical, linear algebra, ODE/PDE convergence, stochastic replication | `universal.py`, `linalg.py`, `convergence.py`+`ode.py`+`pde.py`, `stochastic.py` | met |
| Produce machine-readable evidence | Every `CheckResult` has `metric`/`expected`/`observed`/`evidence`, JSON-serializable | met |
| Execute checks rather than merely recommend | every function in this library performs the actual computation on its arguments (calls `solve_fn`, computes real norms/eigenvalues/CIs) — none are static advice | met |
| Preserve commands and artifacts | the spec script itself is the preserved "command" (plain Python, re-runnable); `Report.save()` persists full evidence to `.trellis/*.json`, never auto-deleted | met |
| Clearly distinguish WARN from FAIL | `_util.threshold_status` gives every threshold-based check a distinct WARN band; `nan_inf_check` has no WARN tier by design (see `references/modules.md`) | met |
| Allow project-specific invariants | `universal.parameter_sanity_check` takes arbitrary predicates; any check function's result can be included in `RESULTS` alongside a hand-built `CheckResult` for a bespoke invariant | met |
| Distinguish implementation / numerical / model_validation / empirical verification | `category` field on every `CheckResult`; `build_report`'s `auto_gaps` flags tiers that never ran | met |

## 4. Manual behavioral check (does an agent actually stop overclaiming)

Automated checks prove the library's math and CLI plumbing are correct;
they don't prove an agent will reach for this instead of declaring victory
once `pytest` is green. Do this once per significant `SKILL.md` change:

1. Introduce a real UC1/UC2/UC3-shaped bug into a small numerical project
   (or reuse the fixtures here as a starting template) alongside unit
   tests that still pass with the bug present.
2. **Baseline**: ask an agent (skill not installed) to verify the change
   is correct. Does it declare success once tests pass?
3. **With skill**: install `trellis/`, repeat. Confirm it
   writes/runs a spec script covering the relevant checks from the
   SKILL.md table, reads the FAIL and `notes`, and does **not** report the
   change as done — and confirm it does not "fix" the FAIL by loosening
   `tol_order`/`tol`/`expected_order` in the spec script itself, which
   would just be UC2 recursively applied to the verification tool. If
   that happens, it's a prompt/workflow problem to fix (make the
   don't-relax-the-tolerance rule more prominent), not a library bug.
4. Confirm a genuinely fixed version of the code produces PASS with the
   *same* spec script (not a loosened one).

## 5. Known limitations (accepted for MVP, revisit if they cause failures)

- **No automatic "classify the computation" step.** Which check functions
  apply is a lookup-table decision for the agent (SKILL.md's table), not
  static analysis of the changed code. Revisit only if agents are
  reliably picking the wrong checks in practice — a heuristic classifier
  is easy to get subtly wrong in a way that's worse than an explicit
  table.
- **`stochastic.py`'s confidence intervals and before/after comparison are
  documented approximations**, not scipy-grade statistics (large-sample
  normal CI, CI-overlap significance heuristic) — see
  `references/modules.md` for exactly where they under/over-cover.
  Nothing stops a spec script from importing scipy directly when a
  borderline call matters.
- **Call-graph-style "does this project even have a reference solution to
  validate against" is not automated** — `model_validation`-tier checks
  require the agent/spec author to actually have or construct one
  (analytic solution, manufactured solution, trusted reference
  implementation). If none exists, the auto-generated `remaining_risks`
  entry is the honest answer, not a library gap.
- **`positive_definite_check` always symmetrizes first** — silently
  correct for genuinely symmetric matrices, but means asymmetry itself
  isn't flagged by this check (pair it with `symmetry_check`).
- Requires numpy; see §0. This is the one skill in the suite with a
  non-stdlib dependency, by deliberate scope decision (see SKILL.md's
  "Why numpy").
