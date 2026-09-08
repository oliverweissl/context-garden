#!/usr/bin/env bash
# Runs the three UC fixture spec scripts through the CLI and asserts on
# their PASS/WARN/FAIL outcomes and evidence, plus direct library-level
# checks against hand-computable ground truth (known finite-difference
# convergence orders, known matrix properties, a known normal
# distribution). Requires numpy in $PYTHON_BIN (default: python3) --
# see SKILL.md.
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIX="$DIR/tests/fixtures"
PY="${PYTHON_BIN:-python3}"

if ! "$PY" -c "import numpy" 2>/dev/null; then
  echo "SKIPPED: numpy not available to $PY -- install it (pip install numpy) or set PYTHON_BIN to an interpreter that has it."
  exit 0
fi

CLI() { "$PY" "$DIR/scripts/trellis.py" "$@"; }

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

# forward difference is 1st order, central difference is 2nd order -- textbook facts
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

# --- UC1: PDE stencil regression --------------------------------------------
rm -rf "$FIX/.trellis"
uc1_bugged=$(SENTINEL_UC1_BUGGED=1 CLI run "$FIX/uc1_pde_stencil_regression.py" 2>&1)
uc1_bugged_exit=$?
assert_contains "UC1 (bugged): overall status FAIL" "$uc1_bugged" "status: FAIL"
assert_contains "UC1 (bugged): names the regressed check" "$uc1_bugged" "stencil_convergence_order"
assert_contains "UC1 (bugged): explains why (order regression)" "$uc1_bugged" "discretization/stencil bug"
assert_eq "UC1 (bugged): CLI exit code is 1" "$uc1_bugged_exit" "1"

uc1_healthy=$(SENTINEL_UC1_BUGGED=0 CLI run "$FIX/uc1_pde_stencil_regression.py" 2>&1)
uc1_healthy_exit=$?
assert_contains "UC1 (healthy): overall status PASS" "$uc1_healthy" "status: PASS"
assert_eq "UC1 (healthy): CLI exit code is 0" "$uc1_healthy_exit" "0"

# --- UC2: solver tolerance masking ------------------------------------------
uc2=$(CLI run "$FIX/uc2_solver_tolerance_masking.py" 2>&1)
uc2_exit=$?
assert_contains "UC2: residual check FAILs against the real scientific tolerance" "$uc2" "residual_norm:solver_residual"
assert_contains "UC2: overall status FAIL despite the 'fix'" "$uc2" "status: FAIL"
assert_contains "UC2: unsupported claim about the relaxed tolerance is surfaced" "$uc2" "relaxed test tolerance"
assert_eq "UC2: CLI exit code is 1" "$uc2_exit" "1"

# --- UC3: single-seed vs multi-seed replication -----------------------------
uc3=$(CLI run "$FIX/uc3_montecarlo_single_seed.py" 2>&1)
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
