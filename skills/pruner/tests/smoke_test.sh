#!/usr/bin/env bash
# Automated smoke test referenced by ../validate.md.
# Copies tests/fixtures/sample_repo to a scratch dir and drives index ->
# select -> expand for the spec's three worked use cases (bug fix, compiler
# failure, solver convergence change). No network, no LLM calls.
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRATCH="$(mktemp -d)"
REPO="$SCRATCH/sample_repo"
cp -R "$DIR/tests/fixtures/sample_repo" "$REPO"
trap 'rm -rf "$SCRATCH"' EXIT

CS() { python3 "$DIR/scripts/pruner.py" --repo "$REPO" "$@"; }

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
assert_not_contains() {
  local desc="$1" haystack="$2" needle="$3"
  if grep -qF -- "$needle" <<<"$haystack"; then
    echo "FAIL: $desc (unexpectedly found: $needle)"
    fail=1
  else
    echo "PASS: $desc"
  fi
}
json_list() { python3 -c "
import json,sys
d = json.load(sys.stdin)
key = sys.argv[1]
print(' '.join(c['id'] for c in d[key]))
" "$1"; }

# --- parser regression checks (bugs caught during development, see
# validate.md section 5) -----------------------------------------------
parser_check=$(python3 -c "
import sys
sys.path.insert(0, '$DIR/scripts')
import pr_cpp, pr_python

cpp = pr_cpp.parse_cpp_file('''
static double clamp(double x, double lo, double hi) {
    if (x < lo) { return lo; }
    return x;
}
int getX() { return 42; }
double residual(
    const int m
) {
    return 0.0;
}
''')
by_name = {s['name']: s for s in cpp['symbols']}
assert 'clamp' not in by_name['clamp']['calls'], 'bug #2 regressed: self-call in signature'
assert by_name['getX']['start_line'] == by_name['getX']['end_line'], 'bug #3 regressed: one-liner never closed'
assert by_name['residual']['start_line'] < by_name['residual']['end_line'] - 1, 'bug #4 regressed: multi-line signature start_line wrong'

py = pr_python.parse_python_file('''
def helper():
    return 1

class C:
    def m(self):
        return helper()
''')
cls = next(s for s in py['symbols'] if s['name'] == 'C')
assert cls['calls'] == [], f'bug #1 regressed: class calls leaked from nested method, got {cls[\"calls\"]}'
print('OK')
" 2>&1)
assert_contains "parser regressions: bugs #1-#4 stay fixed" "$parser_check" "OK"

# --- index -------------------------------------------------------------
index_out=$(CS index)
assert_contains "index: finds 15 files" "$index_out" "indexed 15 file(s)"
assert_contains "index: extracts symbols" "$index_out" "symbols: 17"

reindex_out=$(CS index)
assert_contains "index: second run reuses all files (incremental)" "$reindex_out" "15 unchanged (reused), 0 (re)parsed"

# --- UC1: bug fix (interpolate boundary behavior) -----------------------
uc1=$(CS select --task "Fix incorrect boundary behavior in interpolate()." --budget 2000 --json)
required=$(json_list required_context <<<"$uc1")
supporting=$(json_list supporting_context <<<"$uc1")
tests=$(json_list relevant_tests <<<"$uc1")
config=$(json_list relevant_config <<<"$uc1")
omitted=$(json_list omitted_candidates <<<"$uc1")

assert_contains "UC1: implementation function is required" "$required" "interp/core.py:interpolate"
assert_contains "UC1: a caller (solve_step) is supporting" "$supporting" "interp/solver.py:solve_step"
assert_contains "UC1: a caller (public_api) is supporting" "$supporting" "interp/api.py:public_api"
assert_contains "UC1: the Range type is supporting" "$supporting" "interp/types.py:Range"
assert_contains "UC1: boundary tests are pulled into relevant_tests" "$tests" "test_boundary_clamped_low"
assert_contains "UC1: primary config file included" "$config" "CMakeLists.txt"
assert_contains "UC1: unrelated C++ subsystem correctly omitted" "$omitted" "csrc/solver.cpp:converge"
assert_not_contains "UC1: unrelated C++ subsystem NOT pulled into required" "$required" "csrc/"
assert_not_contains "UC1: unrelated C++ subsystem NOT pulled into supporting" "$supporting" "csrc/"

uc1_used=$(python3 -c "import json,sys; print(json.load(sys.stdin)['used_tokens'])" <<<"$uc1")
uc1_budget=$(python3 -c "import json,sys; print(json.load(sys.stdin)['budget'])" <<<"$uc1")
if [ "$uc1_used" -le "$uc1_budget" ]; then
  echo "PASS: UC1: used_tokens ($uc1_used) respects the hard budget ($uc1_budget)"
else
  echo "FAIL: UC1: used_tokens ($uc1_used) exceeds budget ($uc1_budget)"
  fail=1
fi

# --- UC2: compiler failure (error-stack membership) ----------------------
uc2=$(CS select --task "Fix the build error in the solver" --budget 1500 \
  --error "csrc/solver.cpp:14:5: error: use of undeclared identifier 'residual'" --json)
required2=$(json_list required_context <<<"$uc2")
supporting2=$(json_list supporting_context <<<"$uc2")

assert_contains "UC2: enclosing function of the error line is required" "$required2" "csrc/solver.cpp:residual"
assert_contains "UC2: referenced type (Matrix) is pulled in as supporting" "$supporting2" "csrc/linalg.hpp:Matrix"
assert_contains "UC2: declaration header is pulled in as supporting" "$supporting2" "csrc/solver.hpp"
assert_not_contains "UC2: unrelated Python package NOT pulled into required" "$required2" "interp/"

# --- UC3: solver convergence criterion change -----------------------------
uc3=$(CS select --task "Change the convergence tolerance criterion in converge()" --budget 300 --json)
required3=$(json_list required_context <<<"$uc3")
supporting3=$(json_list supporting_context <<<"$uc3")
tests3=$(json_list relevant_tests <<<"$uc3")

assert_contains "UC3: converge() is required" "$required3" "csrc/solver.cpp:converge"
assert_contains "UC3: its callee residual() is supporting (graph distance)" "$supporting3" "csrc/solver.cpp:residual"
assert_contains "UC3: convergence test is pulled into relevant_tests" "$tests3" "test_converges_below_tolerance"

# --- progressive expansion -------------------------------------------------
slice_id=$(python3 -c "import json,sys; print(json.load(sys.stdin)['slice_id'])" <<<"$uc3")
expand_out=$(CS expand "$slice_id" --add "csrc/solver.hpp:__file__")
assert_contains "expand: promotes a known omitted candidate by id" "$expand_out" "promoted: csrc/solver.hpp:__file__"

overbudget_out=$(CS expand "$slice_id" --file interp/core.py --lines 1:11 2>&1)
assert_contains "expand: ad hoc add over budget is refused without override" "$overbudget_out" "budget"

override_out=$(CS expand "$slice_id" --file interp/core.py --lines 1:11 --budget-extra 200)
assert_contains "expand: ad hoc add succeeds with explicit --budget-extra" "$override_out" "added interp/core.py:1-11"

show_out=$(CS show "$slice_id" --json)
assert_contains "show: reflects the expanded slice" "$show_out" "interp/core.py:1-11"

# --- error handling: no raw Python tracebacks -----------------------------
bad_show=$(CS show s9999 2>&1)
assert_not_contains "show: unknown slice id fails cleanly" "$bad_show" "Traceback (most recent call last)"

bad_expand=$(CS expand "$slice_id" --add "no/such/chunk:id" 2>&1)
assert_contains "expand: unknown chunk id reported, not crashed" "$bad_expand" "unknown chunk id"

echo
if [ "$fail" -eq 0 ]; then
  echo "ALL CHECKS PASSED"
else
  echo "SOME CHECKS FAILED"
fi
exit "$fail"
