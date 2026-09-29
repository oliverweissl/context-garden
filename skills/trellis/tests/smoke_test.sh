#!/usr/bin/env bash
# Runs the three fixtures/uc*.py spec scripts through the CLI and asserts on
# their PASS/WARN/FAIL outcomes and evidence, plus direct library-level
# checks against hand-computable ground truth (known finite-difference
# convergence orders, known matrix properties, a known normal
# distribution). Requires numpy in $PYTHON_BIN (default: python3) --
# see SKILL.md.
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIX="$DIR/tests/fixtures"
PY="${PYTHON_BIN:-python3}"
case "$PY" in */*) PY="$(cd "$(dirname "$PY")" && pwd)/$(basename "$PY")" ;; esac   # tests cd around

if ! "$PY" -c "import numpy" 2>/dev/null; then
  echo "SKIPPED: numpy not available to $PY -- install it (pip install numpy) or set PYTHON_BIN to an interpreter that has it."
  exit 0
fi

TDIR="$(mktemp -d)"   # isolated .trellis dir (spec.lock / baselines) -- never the repo's own
trap 'rm -rf "$TDIR"' EXIT
CLI() { "$PY" "$DIR/scripts/trellis.py" "$@"; }
CLIT() { local sub="$1"; shift; "$PY" "$DIR/scripts/trellis.py" "$sub" --trellis-dir "$TDIR/lockdir" "$@"; }

fail=0
assert_contains() {
  local desc="$1" haystack="$2" needle="$3"
  if grep -qF -- "$needle" <<<"$haystack"; then
    echo "PASS: $desc"
  else
    echo "FAIL: $desc (expected to find: $needle)"
    fail=1
  fi
}
assert_eq() {
  local desc="$1" got="$2" want="$3"
  if [ "$got" = "$want" ]; then
    echo "PASS: $desc"
  else
    echo "FAIL: $desc (got '$got', want '$want')"
    fail=1
  fi
}

# --- library-level checks against hand-computable ground truth -------------
lib_check=$("$PY" -c "
import sys
sys.path.insert(0, '$DIR')
import numpy as np
import trellis as S

# forward difference is 1st order, central difference is 2nd order
x0 = 0.7
true_deriv = np.cos(x0)
def fwd(h): return np.array([(np.sin(x0+h) - np.sin(x0)) / h])
def ctr(h): return np.array([(np.sin(x0+h) - np.sin(x0-h)) / (2*h)])
hs = [0.1, 0.05, 0.025, 0.0125, 0.00625]
r_fwd = S.pde.mesh_convergence(fwd, hs, reference=lambda h: np.array([true_deriv]), expected_order=1.0)
r_ctr = S.pde.mesh_convergence(ctr, hs, reference=lambda h: np.array([true_deriv]), expected_order=2.0)
assert r_fwd.status == 'PASS', r_fwd
assert r_ctr.status == 'PASS', r_ctr
assert abs(r_fwd.metric['observed_order'] - 1.0) < 0.15
assert abs(r_ctr.metric['observed_order'] - 2.0) < 0.15

# a regressed (accidentally 1st-order) scheme judged against expected_order=2 must FAIL
r_regressed = S.pde.mesh_convergence(fwd, hs, reference=lambda h: np.array([true_deriv]), expected_order=2.0)
assert r_regressed.status == 'FAIL', r_regressed

# known matrix properties
assert S.linalg.positive_definite_check(np.eye(3)).status == 'PASS'
assert S.linalg.positive_definite_check(np.array([[1.,2.],[2.,1.]])).status == 'FAIL'  # eigenvalues 3, -1
assert S.linalg.symmetry_check(np.array([[1.,2.],[2.,1.]])).status == 'PASS'
assert S.linalg.symmetry_check(np.array([[1.,2.],[9.,1.]])).status == 'FAIL'

# known normal distribution: 95% CI should bracket the true mean
rng = np.random.RandomState(1)
samples = rng.normal(50.0, 3.0, size=2000)
ci = S.stochastic.confidence_interval_from_samples(samples)
assert ci[0] < 50.0 < ci[1], ci

# NaN must always FAIL, never WARN
assert S.universal.nan_inf_check([1.0, float('nan')]).status == 'FAIL'

print('LIB_OK')
" 2>&1)
assert_contains "library: convergence order matches known finite-difference theory (1st vs 2nd order)" "$lib_check" "LIB_OK"

# --- regression cases for false passes / false fails ------------------------
lib_check2=$("$PY" -c "
import sys
sys.path.insert(0, '$DIR')
import numpy as np
import trellis as S

# Poisson -lap u = f, u = prod sin(pi x_i), N cells per side, 2nd-order FD
def lap1d(N):
    h = 1.0 / N
    return (2*np.eye(N-1) - np.eye(N-1, k=1) - np.eye(N-1, k=-1)) / h**2
def grid(N): return np.arange(1, N) / N
def poisson1d(N, bug=False):
    x = grid(N); h = 1.0 / N
    f = np.pi**2 * np.sin(np.pi * (x + (h if bug else 0.0)))   # bug: O(h) source shift
    return np.linalg.solve(lap1d(N), f)
def poisson2d(N):
    x = grid(N); I = np.eye(N-1); L = lap1d(N)
    A = np.kron(L, I) + np.kron(I, L)
    X, Y = np.meshgrid(x, x, indexing='ij')
    return np.linalg.solve(A, (2*np.pi**2*np.sin(np.pi*X)*np.sin(np.pi*Y)).ravel())
exact1d = lambda N: np.sin(np.pi * grid(N))
def exact2d(N):
    X, Y = np.meshgrid(grid(N), grid(N), indexing='ij')
    return (np.sin(np.pi*X)*np.sin(np.pi*Y)).ravel()
Ns = [8, 16, 32, 64]
r = S.pde.mesh_convergence(poisson1d, Ns, reference=exact1d, expected_order=2)
assert r.status == 'PASS' and r.evidence['param_kind'] == 'resolution', r
r = S.pde.mesh_convergence(poisson2d, [8, 16, 32], reference=exact2d, expected_order=2)
assert r.status == 'PASS', r   # RMS norm: no d/2 order loss in 2D
r = S.pde.mesh_convergence(lambda N: poisson1d(N, bug=True), Ns, reference=exact1d, expected_order=2)
assert r.status == 'FAIL', r
# self-referential (no reference): 1st-order scheme must FAIL vs 2, and read ~1
fo = lambda h: np.array([1.0 + h])
r = S.pde.mesh_convergence(fo, [0.08, 0.04, 0.02, 0.01], expected_order=2)
assert r.status == 'FAIL' and abs(r.observed - 1.0) < 0.05, r
assert S.pde.mesh_convergence(fo, [0.08, 0.04, 0.02, 0.01], expected_order=1).status == 'PASS'
# self-referential on the real 2nd-order solver, restricted to the coarse grid's points
restrict = lambda c, f: float(np.sqrt(np.mean((c - f[1::2])**2)))
r = S.pde.mesh_convergence(poisson1d, Ns, expected_order=2, error_fn=restrict)
assert r.status == 'PASS', r
r = S.pde.mesh_convergence(lambda N: poisson1d(N, bug=True), Ns + [128, 256], expected_order=2, error_fn=restrict)
assert r.status == 'FAIL', r
# round-off floor: not a FAIL
x0 = 0.7; ctr = lambda h: np.array([(np.sin(x0+h) - np.sin(x0-h)) / (2*h)])
r = S.pde.mesh_convergence(ctr, [10.0**-k for k in range(1, 8)], reference=lambda h: np.array([np.cos(x0)]), expected_order=2)
assert r.status == 'WARN', r

# Monte Carlo
biased = lambda s: np.random.default_rng(s).normal(0, 1, 10000).mean() + 0.5
assert S.stochastic.seed_replication_check(biased, list(range(10)), expected=0.0).status == 'FAIL'
unbiased = lambda s: np.random.default_rng(s).normal(0, 1, 10000).mean()
assert S.stochastic.seed_replication_check(unbiased, list(range(10)), expected=0.0).status == 'PASS'
assert S.stochastic.seed_replication_check(unbiased, list(range(10))).status == 'WARN'   # spread only
nanfn = lambda s: np.nan if s == 3 else np.random.default_rng(s).normal()
assert S.stochastic.seed_replication_check(nanfn, list(range(10)), expected=0.0).status == 'FAIL'
assert S.stochastic.compare_before_after([1.00], [1.01]).status != 'PASS'
reg = S.stochastic.compare_before_after([10, 10.1, 9.9, 10.05], [5, 5.1, 4.9, 5.05], direction='higher_is_better')
assert reg.status == 'FAIL', reg
assert S.stochastic.compare_before_after([10, 10.1, 9.9, 10.05], [5, 5.1, 4.9, 5.05], direction='lower_is_better').status == 'PASS'
try:
    S.stochastic.compare_before_after([1, 2], [3, 4], confidence=0.999); raise SystemExit('no ValueError')
except ValueError:
    pass

# linear algebra
A = np.array([[4., 1.], [1., 3.]])
assert S.linalg.residual_norm(A, np.zeros(2), np.array([1e-9, 2e-9]), tol=1e-6).status == 'FAIL'
b = np.array([1e8, 2e8]); assert S.linalg.residual_norm(A, np.linalg.solve(A, b), b, tol=1e-6).status == 'PASS'
n = 12; H = 1 / (np.arange(n)[:, None] + np.arange(n) + 1); bb = H @ np.ones(n)
assert S.linalg.residual_norm(H, np.linalg.solve(H, bb), bb, tol=1e-6, cond=np.linalg.cond(H)).status == 'WARN'
assert S.linalg.positive_definite_check(np.diag([1, 1e-17])).status == 'FAIL'

# reproducibility: deterministic NaN is reproducible (not FAIL) but flagged WARN
r = S.universal.reproducibility_check(lambda: np.array([1.0, np.nan]))
assert r.status == 'WARN' and r.metric['n_nan'] == 3 and 'NaN' in r.notes, r
assert S.universal.reproducibility_check(lambda: np.array([1.0, 2.0])).status == 'PASS'
it = iter([np.array([1.0, np.nan]), np.array([1.0, 3.0]), np.array([1.0, np.nan])])
assert S.universal.reproducibility_check(lambda: next(it)).status == 'FAIL'
print('LIB2_OK')
" 2>&1)
assert_contains "library: false-pass/false-fail regressions (N vs h, 2D RMS, self-ref, MC bias, Welch, relative residual, NaN reproducibility)" "$lib_check2" "LIB2_OK"
[ "${VERBOSE:-0}" = 1 ] && echo "$lib_check2"

# --- strict thresholds (no WARN slack band) ---------------------------------
strict=$("$PY" -c "
import sys
sys.path.insert(0, '$DIR')
import numpy as np
import trellis as S
from trellis._util import threshold_status
assert threshold_status(2e-6, 1e-6).value == 'FAIL' and threshold_status(1e-6, 1e-6).value == 'PASS'
assert threshold_status(float('nan'), 1.0).value == 'FAIL'
A = np.eye(2); b = np.array([1.0, 0.0])
x = b + np.array([3e-6, 0.0])                      # relative residual ~1.5e-6: 1.5x tol
assert S.linalg.residual_norm(A, x, b, tol=1e-6).status == 'FAIL'          # was WARN
assert S.ode.reference_solution_comparison([1.0 + 2e-6], [1.0], tol=1e-6).status == 'FAIL'
assert S.ode.invariant_preservation([1.0, 1.0 + 3e-6], lambda s: s, tol=1e-6).status == 'FAIL'
assert S.pde.conservation_check([1.0, 1.0 + 3e-6], lambda s: s, tol=1e-6).status == 'FAIL'
assert S.pde.conservation_check([1.0, 1.0 + 5e-7], lambda s: s, tol=1e-6).status == 'PASS'
assert S.optimization.constraint_violation_check([0.0], [lambda x: 2e-6], tol=1e-6).status == 'FAIL'
assert S.linalg.reconstruction_error(np.eye(2), np.eye(2) * (1 + 2e-6), tol=1e-6).status == 'FAIL'
assert S.universal.magnitude_check(10.5, (1.0, 10.0)).status == 'FAIL'
# stabilised order 1.5 vs expected 2 with tol_order 0.3 -> FAIL
p15 = lambda h: np.array([1.0 + h**1.5])
r = S.pde.mesh_convergence(p15, [0.1, 0.05, 0.025, 0.0125], reference=np.array([1.0]), expected_order=2.0)
assert r.status == 'FAIL', r
# indeterminate cases stay WARN: pre-asymptotic / round-off floor / spread-only replication
x0 = 0.7; ctr = lambda h: np.array([(np.sin(x0+h) - np.sin(x0-h)) / (2*h)])
assert S.pde.mesh_convergence(ctr, [10.0**-k for k in range(1, 8)], reference=lambda h: np.array([np.cos(x0)]), expected_order=2).status == 'WARN'
print('STRICT_OK')
" 2>&1)
assert_contains "8.1 strict: value > tol is FAIL for residual/reference/drift/constraint/order; WARN only if indeterminate" "$strict" "STRICT_OK"

# --- distribution_sanity_check statistics -----------------------------------
dist=$("$PY" -c "
import sys
sys.path.insert(0, '$DIR')
import numpy as np
import trellis as S
st = S.stochastic
# t-test with n-scaled standard error: bias 0.15 at n=100k must FAIL
r = st.distribution_sanity_check(np.random.default_rng(0).normal(0.15, 1, 100000), expected_mean=0.0)
assert r.status == 'FAIL' and 'mean' in r.notes, r
# chi-square (Wilson-Hilferty): sigma 1.02 instead of 1 at n=100k must FAIL
assert st.distribution_sanity_check(np.random.default_rng(0).normal(0, 1.02, 100000), expected_std=1.0).status == 'FAIL'
# KS vs CDF: a uniform with the right mean/std is not normal
u = np.random.default_rng(0).uniform(-3**0.5, 3**0.5, 100000)
r = st.distribution_sanity_check(u, expected_mean=0, expected_std=1, reference=st.normal_cdf)
assert r.status == 'FAIL' and 'KS' in r.notes and r.metric['n_tests'] == 3, r
assert abs(r.metric['alpha_per_test'] - 0.01/3) < 1e-12        # Bonferroni over the 3 tests
# two-sample KS against a reference sample
ref = np.random.default_rng(99).normal(0, 1, 20000)
assert st.distribution_sanity_check(np.random.default_rng(1).normal(0, 1, 20000), reference=ref).status == 'PASS'
assert st.distribution_sanity_check(np.random.default_rng(1).standard_t(3, 20000), reference=ref).status == 'FAIL'
# known p-values
assert abs(st.t_two_sided_p(2.0, 10) - 0.0734) < 1e-3 and abs(st.kolmogorov_sf(1.36) - 0.0495) < 1e-3
# false-fail rate of a correct N(0,1) sampler, 200 fixed seeds, all three tests
ff = sum(st.distribution_sanity_check(np.random.default_rng(s).normal(0, 1, 10000), expected_mean=0,
         expected_std=1, reference=st.normal_cdf).status == 'FAIL' for s in range(200))
# non-normal but correct sampler (exponential): kurtosis-adjusted chi-square must not false-fail
ffe = sum(st.distribution_sanity_check(np.random.default_rng(s).exponential(2.0, 10000), expected_mean=2,
          expected_std=2).status == 'FAIL' for s in range(200))
assert ff <= 6 and ffe <= 6, (ff, ffe)
assert st.distribution_sanity_check([1.0]).status == 'WARN'
print(f'DIST_OK normal_false_fail={ff}/200 exponential_false_fail={ffe}/200')
" 2>&1)
assert_contains "8.2 distribution: t-test/chi2/KS + Bonferroni; bias FAILs; false-fail rate <= 3%" "$dist" "DIST_OK"
echo "      ($(grep -o 'normal_false_fail.*' <<<"$dist"))"

# --- gradient_check near stationary points ----------------------------------
grad=$("$PY" -c "
import sys
sys.path.insert(0, '$DIR')
import numpy as np
import trellis as S
gc = S.optimization.gradient_check
rosen = lambda x: 100*(x[1]-x[0]**2)**2 + (1-x[0])**2
rgrad = lambda x: np.array([-400*x[0]*(x[1]-x[0]**2) - 2*(1-x[0]), 200*(x[1]-x[0]**2)])
# stationary / near-stationary points: correct gradient must not FAIL
for x in ([1.0, 1.0], [1.0 + 1e-9, 1.0 - 1e-9], [1.0 + 1e-7, 1.0]):
    r = gc(rosen, rgrad, x); assert r.status == 'PASS', (x, r)
off = lambda x: 1e6 + np.sum(x**2)            # |f| >> |g| h: FD cancellation dominates
r = gc(off, lambda x: 2*x, np.array([1e-8, -2e-8])); assert r.status == 'PASS', r
assert r.evidence['floor_limited_coordinates'], r
r = gc(lambda x: np.cos(x[0]) * 1e3, lambda x: np.array([-1e3*np.sin(x[0])]), [np.pi]); assert r.status == 'PASS', r
# 0.5% wrong gradient away from stationary points must FAIL
bad = lambda x: rgrad(x) * np.array([1.005, 1.0])
assert gc(rosen, bad, [0.3, -1.2]).status == 'FAIL'
assert gc(rosen, rgrad, [0.3, -1.2]).status == 'PASS'
assert gc(off, lambda x: 2*x*1.005, np.array([3.0, -2.0])).status == 'FAIL'
# a wrong gradient AT a stationary point (claims slope where there is none) still FAILs
assert gc(rosen, lambda x: rgrad(x) + np.array([1e-3, 0]), [1.0, 1.0]).status == 'FAIL'
print('GRAD_OK')
" 2>&1)
assert_contains "8.3 gradient_check: no false FAIL near stationary points; 0.5% wrong gradient FAILs" "$grad" "GRAD_OK"

# --- spec lock ----------------------------------------------------------------
LK="$TDIR/lockspec"; mkdir -p "$LK"
cat >"$LK/spec.py" <<EOF
import os, sys; sys.path.insert(0, "$DIR")
import numpy as np
import trellis as S
E = os.environ.get
x0 = 0.7
ctr = lambda h: np.array([(np.sin(x0+h) - np.sin(x0-h)) / (2*h)])
REF = float(E("REF", str(np.cos(x0))))
RESULTS = [
    S.pde.mesh_convergence(ctr, [0.1, 0.05, 0.025, 0.0125], reference=lambda h: np.array([REF]),
                           expected_order=float(E("ORDER", "2")), tol_order=float(E("TOL_ORDER", "0.3")), name="order"),
    S.linalg.residual_norm(np.eye(2), np.ones(2), np.ones(2), tol=float(E("TOL", "1e-8")), name="res"),
    S.stochastic.distribution_sanity_check(np.random.default_rng(0).normal(0, 1, 1000), expected_mean=0,
                                           alpha=float(E("ALPHA", "0.01")), name="dist"),
]
if E("DROP") != "1":
    RESULTS.append(S.universal.nan_inf_check([1.0], name="finite"))
EOF
LCLI() {
  if [ "$1" = run ]; then set -- run --save "$LK/r.json" "${@:2}"; fi
  "$PY" "$DIR/scripts/trellis.py" "$1" "$LK/spec.py" --trellis-dir "$LK/.trellis" "${@:2}"
}
out=$(LCLI run 2>&1); ec=$?
assert_contains "9 lock: no lock -> WARN 'unpinned spec'" "$out" "Unpinned spec"
assert_eq "9 lock: unpinned exit code 3" "$ec" "3"
LCLI lock >/dev/null 2>&1
lockjson=$("$PY" -c "
import json; d = json.load(open('$LK/.trellis/spec.lock'))
(spec,) = d['specs']; c = d['specs'][spec]['checks']
o = c['order']['config']; assert o['expected_order'] == 2.0 and o['tol_order'] == 0.3 and o['param_values'] == [0.1, 0.05, 0.025, 0.0125]
assert len(o['reference']['sha256']) == 64
assert c['residual_norm:res']['config']['tol'] == 1e-8 and c['distribution_sanity:dist']['config']['alpha'] == 0.01
print('LOCKJSON_OK')")
assert_contains "9 lock: spec.lock records orders, tolerances, alpha, resolutions, reference sha256" "$lockjson" "LOCKJSON_OK"
out=$(LCLI run 2>&1); ec=$?
assert_eq "9 lock: unchanged spec -> exit 0" "$ec" "0"
out=$(TOL=1e-6 LCLI run 2>&1); ec=$?
assert_contains "9 lock: larger tol -> FAIL with diff" "$out" "[LOOSER] residual_norm:res: tol 1e-08 -> 1e-06"
assert_eq "9 lock: loosened exit code 1" "$ec" "1"
out=$(ORDER=1 LCLI run 2>&1); assert_contains "9 lock: lower expected order -> FAIL" "$out" "[LOOSER] order: expected_order 2.0 -> 1.0"
out=$(TOL_ORDER=0.6 LCLI run 2>&1); assert_contains "9 lock: larger tol_order -> FAIL" "$out" "[LOOSER] order: tol_order"
out=$(ALPHA=0.001 LCLI run 2>&1); assert_contains "9 lock: smaller alpha (fewer rejections) -> FAIL" "$out" "[LOOSER] distribution_sanity:dist: alpha"
out=$(DROP=1 LCLI run 2>&1); assert_contains "9 lock: removed check -> FAIL" "$out" "check REMOVED"
out=$(REF=0.76 LCLI run 2>&1); assert_contains "9 lock: changed reference hash -> FAIL" "$out" "[LOOSER] order: reference"
out=$(TOL=1e-10 LCLI run 2>&1); ec=$?
assert_contains "9 lock: tighter tol reported" "$out" "[tighter] residual_norm:res: tol"
assert_eq "9 lock: tighter change is allowed (exit 0)" "$ec" "0"
out=$(TOL=1e-6 LCLI run --update-lock 2>&1); ec=$?
assert_contains "9 lock: --update-lock rewrites and flags human review" "$out" "HUMAN must review"
assert_eq "9 lock: --update-lock with loosening is WARN (exit 3), never silently green" "$ec" "3"
out=$(TOL=1e-6 LCLI run 2>&1); ec=$?
assert_eq "9 lock: after human-accepted update the looser spec passes" "$ec" "0"

# --- results from files ---------------------------------------------------------
IO="$TDIR/io"; mkdir -p "$IO/out"
iochk=$(cd "$TDIR" && "$PY" -c "
import sys, json, os
sys.path.insert(0, '$DIR')
import numpy as np
import trellis as S
def lap(N):
    h = 1.0/N; return (2*np.eye(N-1) - np.eye(N-1, k=1) - np.eye(N-1, k=-1)) / h**2
grid = lambda N: np.arange(1, N) / N
solve = lambda N: np.linalg.solve(lap(N), np.pi**2*np.sin(np.pi*grid(N)))
exact = lambda N: np.sin(np.pi*grid(N))
Ns = [8, 16, 32, 64]
for N in Ns:                                   # as a C++/Fortran/SLURM job would leave them
    u = solve(N)
    np.save(f'io/out/n{N}.npy', u)
    np.savez(f'io/out/f{N}.npz', u=u, x=grid(N))
    with open(f'io/out/p{N}.csv', 'w') as fh:
        fh.write('x,u\n'); [fh.write(f'{float(a)!r},{float(b)!r}\n') for a, b in zip(grid(N), u)]
    np.savetxt(f'io/out/w{N}.dat', np.column_stack([grid(N), u]))
    np.save(f'io/out/exact{N}.npy', exact(N))
for tmpl, key in [('io/out/n{N}.npy', None), ('io/out/f{N}.npz', 'u'), ('io/out/p{N}.csv', 'u'), ('io/out/w{N}.dat', 1)]:
    r = S.pde.mesh_convergence(tmpl, Ns, reference='io/out/exact{N}.npy', expected_order=2, file_key=key)
    assert r.status == 'PASS', (tmpl, r)
    assert r.config['solve_fn'] == tmpl
# precomputed errors per resolution (JSON), several layouts
errs2 = {N: float(np.sqrt(np.mean((solve(N) - exact(N))**2))) for N in Ns}
json.dump({str(N): {'error': e, 'residual': 1e-12} for N, e in errs2.items()}, open('io/errs.json', 'w'))
r = S.pde.mesh_convergence(None, errors='io/errs.json', error_key='error', expected_order=2); assert r.status == 'PASS', r
json.dump({'levels': [{'N': N, 'err': 1.0/N} for N in Ns]}, open('io/errs1.json', 'w'))
from trellis.io import load_errors
assert load_errors('io/errs1.json', key='err', root='levels') == {8.0: 0.125, 16.0: 0.0625, 32.0: 0.03125, 64.0: 0.015625}
r = S.pde.mesh_convergence(None, Ns, errors=load_errors('io/errs1.json', key='err', root='levels'), expected_order=2)
assert r.status == 'FAIL', r                     # 1st-order errors vs expected 2
json.dump({'N': Ns, 'errors': list(errs2.values())}, open('io/errs2.json', 'w'))
assert S.pde.mesh_convergence(None, errors='io/errs2.json', expected_order=2).status == 'PASS'
# JSON scalars with a key path
json.dump({'stats': {'residual': 3e-9, 'hist': [1, 2]}}, open('io/res.json', 'w'))
assert S.universal.threshold_check('io/res.json', 1e-8, key='stats.residual').status == 'PASS'
assert S.universal.threshold_check('io/res.json', 1e-9, key='stats.residual').status == 'FAIL'
assert float(S.io.load('io/res.json', key='stats.hist.1')) == 2.0
assert S.universal.nan_inf_check('io/out/n8.npy').status == 'PASS'
print('IO_OK')
" 2>&1)
assert_contains "10.6 io: convergence from npy/npz/CSV/whitespace files, JSON precomputed errors, JSON scalar key paths" "$iochk" "IO_OK"
# relative paths in a spec resolve against the spec's own directory
cat >"$IO/spec.py" <<EOF
import sys; sys.path.insert(0, "$DIR")
import trellis as S
RESULTS = [S.pde.mesh_convergence(None, errors="errs.json", error_key="error", expected_order=2, name="from_json")]
EOF
out=$(cd / && "$PY" "$DIR/scripts/trellis.py" run "$IO/spec.py" --trellis-dir "$IO/.trellis" --allow-warn 2>&1)
assert_contains "10.6 io: spec-relative result paths resolve from any cwd" "$out" "[PASS] from_json"

# --- sparse / LinearOperator ----------------------------------------------------
nosp=$("$PY" -c "
import sys
sys.path.insert(0, '$DIR')
import numpy as np
import trellis as S
class Op:                                        # duck-typed operator: numpy-only path
    shape = (3, 3)
    def matvec(self, v): return 2.0 * v
    def rmatvec(self, v): return 2.0 * v
r = S.linalg.residual_norm(Op(), np.ones(3) / 2, np.ones(3), tol=1e-12)
assert r.status == 'PASS' and abs(r.metric['matrix_norm'] - 2.0) < 1e-9, r
assert S.linalg.residual_norm(Op(), np.ones(3), np.ones(3), tol=1e-6).status == 'FAIL'
try:
    import scipy  # noqa
    print('NOSP_OK (scipy present; fake-sparse test skipped)')
except ImportError:
    Fake = type('csr_matrix', (), {'__module__': 'scipy.sparse._csr', 'shape': (2, 2)})
    try:
        S.linalg.residual_norm(Fake(), np.ones(2), np.ones(2), tol=1e-8); print('no error')
    except ImportError as e:
        assert 'requires scipy' in str(e), e
        print('NOSP_OK')
" 2>&1)
assert_contains "10.7 linalg: duck-typed operator works numpy-only; sparse input without scipy -> clear ImportError" "$nosp" "NOSP_OK"
SPY="${SCIPY_PYTHON_BIN:-$PY}"
if "$SPY" -c "import scipy, numpy" 2>/dev/null; then
sp=$("$SPY" -c "
import sys
sys.path.insert(0, '$DIR')
import numpy as np, scipy.sparse as sp, scipy.sparse.linalg as spla
import trellis as S
n = 40
T = sp.diags([-1, 2, -1], [-1, 0, 1], shape=(n, n))
A = (sp.kron(T, sp.eye(n)) + sp.kron(sp.eye(n), T)).tocsr()       # 1600x1600 2D Laplacian
b = np.ones(A.shape[0]); x = spla.spsolve(A.tocsc(), b)
for o in (2, 1, np.inf, 'fro'):
    r = S.linalg.residual_norm(A, x, b, tol=1e-12, ord=o); assert r.status == 'PASS', (o, r)
assert abs(S.linalg.residual_norm(A, x, b, tol=1, ord=1).metric['matrix_norm'] - 8.0) < 1e-12
assert S.linalg.residual_norm(A, x * (1 + 1e-4), b, tol=1e-8).status == 'FAIL'
op = spla.aslinearoperator(A)
assert S.linalg.residual_norm(op, x, b, tol=1e-12).status == 'PASS'
noadj = spla.LinearOperator(A.shape, matvec=lambda v: A @ v, dtype=float)
r = S.linalg.residual_norm(noadj, x, b, tol=1e-12); assert r.status == 'PASS' and 'probe' in r.metric['matrix_norm_method'], r
# condition estimate without densifying vs exact dense 1-norm condition number
c = S.linalg.conditioning(A); exact = np.linalg.cond(A.toarray(), 1)
assert c.metric['method'].startswith('1-norm estimate') and exact / 3 <= c.observed <= exact * 1.0001, (c.observed, exact)
sing = sp.csr_matrix(np.array([[1.0, 2.0], [2.0, 4.0]]))
assert S.linalg.conditioning(sing).status == 'FAIL'
assert S.linalg.conditioning(op).status == 'WARN'                    # no inverse: skipped, not verified
lu = spla.splu(A.tocsc())
inv = spla.LinearOperator(A.shape, matvec=lu.solve, rmatvec=lambda y: lu.solve(y, trans='T'), dtype=float)
assert S.linalg.conditioning(op, inverse=inv).observed > 100
assert S.linalg.symmetry_check(A).status == 'PASS'
assert S.linalg.symmetry_check(A + sp.eye(A.shape[0], k=1) * 1e-3).status == 'FAIL'
assert S.linalg.symmetry_check(op).status == 'PASS'
assert S.linalg.positive_definite_check(A).status == 'PASS'
assert S.linalg.positive_definite_check(A - 9.0 * sp.eye(A.shape[0])).status == 'FAIL'
assert S.linalg.reconstruction_error(A, A.copy(), tol=1e-14).status == 'PASS'
print('SPARSE_OK')
" 2>&1)
assert_contains "10.7 linalg: scipy.sparse/LinearOperator residuals, onenormest cond (no densify), symmetry, PD" "$sp" "SPARSE_OK"
else
  echo "SKIPPED: 10.7 scipy paths (scipy not available to $SPY; set SCIPY_PYTHON_BIN)"
fi

# --- per-commit baselines --------------------------------------------------------
BG="$TDIR/bgrepo"; mkdir -p "$BG/verify"
cat >"$BG/verify/spec.py" <<EOF
import os, sys; sys.path.insert(0, "$DIR")
import numpy as np
import trellis as S
x0 = 0.7
BUG = os.environ.get("BUG") == "1"
def d(h):
    if BUG:   # order drops; the spec itself does not judge the order, the baseline does
        return np.array([(np.sin(x0+h) - np.sin(x0-h)) / (2*h) + 0.05*h**1.2])
    return np.array([(np.sin(x0+h) - np.sin(x0-h)) / (2*h)])
RES = float(os.environ.get("RES", "1e-12"))
RESULTS = [
    S.pde.mesh_convergence(d, [0.1, 0.05, 0.025, 0.0125], reference=lambda h: np.array([np.cos(x0)]),
                           expected_order=None, tol_order=0.3, name="order"),   # order reported, not judged
    S.universal.threshold_check(RES, 1e-8, name="res", metric_name="residual"),
]
EOF
git -C "$BG" init -q && git -C "$BG" add -A && git -C "$BG" -c user.email=t@t -c user.name=t commit -qm init
sha=$(git -C "$BG" rev-parse HEAD)
BCLI() { "$PY" "$DIR/scripts/trellis.py" run "$BG/verify/spec.py" --save "$BG/r.json" --allow-warn "$@"; }
"$PY" "$DIR/scripts/trellis.py" lock "$BG/verify/spec.py" >/dev/null 2>&1
assert_eq "10.8/9 lock: default lock location is <git root>/.trellis/spec.lock" "$(test -f "$BG/.trellis/spec.lock" && echo yes)" "yes"
BCLI --save-baseline >/dev/null 2>&1
assert_eq "10.8 baseline: --save-baseline writes .trellis/baselines/<git-sha>.json" "$(test -f "$BG/.trellis/baselines/$sha.json" && echo yes)" "yes"
out=$(BCLI --baseline latest 2>&1); ec=$?
assert_eq "10.8 baseline: unchanged code -> no regression (exit 0)" "$ec" "0"
out=$(BUG=1 BCLI --baseline latest 2>&1); ec=$?
assert_contains "10.8 baseline: order drop beyond tol_order FAILs (spec reports order without judging it)" "$out" "observed order dropped"
assert_eq "10.8 baseline: regression exit code 1" "$ec" "1"
out=$(RES=5e-12 BCLI --baseline "${sha:0:8}" 2>&1)
assert_contains "10.8 baseline: residual grew > factor FAILs (sha prefix)" "$out" "residual grew"
out=$(RES=5e-12 BCLI --baseline latest --regression-factor 10 2>&1); ec=$?
assert_eq "10.8 baseline: --regression-factor is configurable" "$ec" "0"
out=$(BCLI --baseline deadbeef 2>&1)
assert_contains "10.8 baseline: unknown baseline -> WARN, not silent" "$out" "No baseline comparison"

# --- UC1: PDE stencil regression --------------------------------------------
rm -rf "$FIX/.trellis"
SENTINEL_UC1_BUGGED=0 CLIT lock "$FIX/uc1_pde_stencil_regression.py" >/dev/null 2>&1
uc1_bugged=$(SENTINEL_UC1_BUGGED=1 CLIT run "$FIX/uc1_pde_stencil_regression.py" 2>&1)
uc1_bugged_exit=$?
assert_contains "UC1 (bugged): overall status FAIL" "$uc1_bugged" "status: FAIL"
assert_contains "UC1 (bugged): names the regressed check" "$uc1_bugged" "stencil_convergence_order"
assert_contains "UC1 (bugged): explains why (order regression)" "$uc1_bugged" "discretization/stencil bug"
assert_eq "UC1 (bugged): CLI exit code is 1" "$uc1_bugged_exit" "1"

uc1_healthy=$(SENTINEL_UC1_BUGGED=0 CLIT run "$FIX/uc1_pde_stencil_regression.py" 2>&1)
uc1_healthy_exit=$?
assert_contains "UC1 (healthy): overall status PASS" "$uc1_healthy" "status: PASS"
assert_eq "UC1 (healthy): CLI exit code is 0" "$uc1_healthy_exit" "0"

# --- UC2: solver tolerance masking ------------------------------------------
uc2=$(CLIT run "$FIX/uc2_solver_tolerance_masking.py" 2>&1)
uc2_exit=$?
assert_contains "UC2: residual check FAILs against the real scientific tolerance" "$uc2" "residual_norm:solver_residual"
assert_contains "UC2: overall status FAIL despite the 'fix'" "$uc2" "status: FAIL"
assert_contains "UC2: unsupported claim about the relaxed tolerance is surfaced" "$uc2" "relaxed test tolerance"
assert_eq "UC2: CLI exit code is 1" "$uc2_exit" "1"

# --- UC3: single-seed vs multi-seed replication -----------------------------
CLIT lock "$FIX/uc3_montecarlo_single_seed.py" >/dev/null 2>&1
uc3=$(CLIT run "$FIX/uc3_montecarlo_single_seed.py" 2>&1)
uc3_exit=$?
assert_contains "UC3: seed replication check ran across 20 seeds" "$uc3" "n_seeds': 20"
assert_contains "UC3: overall status PASS (mean is close to the true no-improvement baseline)" "$uc3" "status: PASS"
assert_eq "UC3: CLI exit code is 0" "$uc3_exit" "0"

# --- auto-gap detection: unsupported_claims / remaining_risks --------------
assert_contains "UC1: flags missing model_validation risk automatically" "$uc1_healthy" "No model/reference-solution validation"
assert_contains "UC3: flags missing numerical-correctness claim automatically" "$uc3" "No numerical-correctness checks"

# --- report persistence: saved JSON is valid and matches CLI status --------
report_status=$("$PY" -c "
import json
d = json.load(open('$FIX/.trellis/uc2_solver_tolerance_masking_report.json'))
print(d['status'])
")
assert_eq "report: saved JSON status matches CLI output" "$report_status" "FAIL"

# --- WARN is not success: nonzero exit unless --allow-warn ------------------
warn_dir="$(mktemp -d)"
cat >"$warn_dir/warn_spec.py" <<EOF
import sys; sys.path.insert(0, "$DIR")
import trellis as S
RESULTS = [S.stochastic.compare_before_after([1.0], [1.1])]
EOF
PYTHONDONTWRITEBYTECODE=1 CLIT run "$warn_dir/warn_spec.py" --save "$warn_dir/report.json" >/dev/null 2>&1; warn_exit=$?
assert_eq "WARN report: CLI exit code is 3" "$warn_exit" "3"
PYTHONDONTWRITEBYTECODE=1 CLIT run "$warn_dir/warn_spec.py" --save "$warn_dir/report.json" --allow-warn >/dev/null 2>&1; warn_exit=$?
assert_eq "WARN report with --allow-warn: CLI exit code is 0" "$warn_exit" "0"
rm -rf "$warn_dir"

# --- clean error handling ---------------------------------------------------
bad_run=$(CLI run "$FIX/does_not_exist.py" 2>&1)
assert_contains "run: missing spec file fails cleanly" "$bad_run" "no such spec file"

rm -rf "$FIX/.trellis"

echo
if [ "$fail" -eq 0 ]; then
  echo "ALL CHECKS PASSED"
else
  echo "SOME CHECKS FAILED"
fi
exit "$fail"
