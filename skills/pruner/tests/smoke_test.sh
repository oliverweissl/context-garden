#!/usr/bin/env bash
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

# --- parser regression checks (bugs caught during development) --------
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

# --- parser fixes: literals/comments, ctor init list, operators, decorators
cat >"$SCRATCH/fix.cpp" <<'EOF'
const char* s = "{ oops"; // {
/* { */ char c = '{'; auto r = R"x( { )x";
int after() { return 1; }
struct Vec {
  Vec(int n) : data_(n), size_(n) {
  }
  bool operator==(const Vec& o) const { return true; }
  double& operator[](int i) { return d; }
  int operator()(int x) { return x; }
};
std::ostream& operator<<(std::ostream& os, const Vec& v) {
  return os;
}
EOF
printf 'import functools\n\n@functools.cache\n@staticmethod\ndef f():\n    return 1\n' >"$SCRATCH/fix.py"
fix_check=$(python3 -c "
import sys
sys.path.insert(0, '$DIR/scripts')
import pr_cpp, pr_python
cpp = pr_cpp.parse_cpp_file(open('$SCRATCH/fix.cpp').read())
names = {s['name']: s for s in cpp['symbols'] if s['type'] == 'function'}
assert names.get('after', {}).get('start_line') == 3, ('brace in string/comment', names)
assert 'Vec' in names and 'size_' not in names, ('ctor init list', names)
for op in ['operator==', 'operator[]', 'operator()', 'operator<<']:
    assert op in names, (op, names)
py = pr_python.parse_python_file(open('$SCRATCH/fix.py').read())
f = next(s for s in py['symbols'] if s['name'] == 'f')
assert f['start_line'] == 3, ('decorator not in range', f)
print('OK')
" 2>&1)
assert_contains "parser fixes: string/comment braces, ctor init list, operators, decorators" "$fix_check" "OK"

# --- whole-file chunk end_line, symlink dedupe, slice staleness warning ---
MINI="$SCRATCH/mini"
mkdir -p "$MINI/pkg"
printf 'a = 1\nb = 2\nc = 3\n' >"$MINI/pkg/consts.py"
printf 'def widget_frobnicate():\n    return 1\n' >"$MINI/pkg/widget.py"
ln -s widget.py "$MINI/pkg/widget_link.py"
M() { python3 "$DIR/scripts/pruner.py" --repo "$MINI" "$@"; }
M index >/dev/null
mini_check=$(python3 -c "
import sqlite3
db = sqlite3.connect('$MINI/.pruner/index.sqlite')
lc = dict(db.execute('SELECT path, line_count FROM files'))
assert lc['pkg/consts.py'] == 3, lc
assert 'pkg/widget_link.py' not in lc, 'symlink indexed twice'
print('OK')
" 2>&1)
assert_contains "whole-file line_count exact; symlink deduped" "$mini_check" "OK"
mini_sel=$(M select --task "fix widget_frobnicate" --json)
mini_id=$(python3 -c "import json,sys; print(json.load(sys.stdin)['slice_id'])" <<<"$mini_sel")
fresh=$(M show "$mini_id" 2>&1 >/dev/null)
assert_not_contains "show: no staleness warning when unchanged" "$fresh" "changed since slice"
echo "# edit" >>"$MINI/pkg/widget.py"
stale=$(M show "$mini_id" 2>&1 >/dev/null)
assert_contains "show: warns when a slice file changed" "$stale" "pkg/widget.py"

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
assert_contains "UC3: declaration header (mentions converge) is selected" "$(json_list supporting_context <<<"$uc3")" "csrc/solver.hpp:__file__"
read -r omit_id omit_tok < <(python3 -c "import json,sys; c=json.load(sys.stdin)['omitted_candidates'][0]; print(c['id'], c['tokens'])" <<<"$uc3")
expand_out=$(CS expand "$slice_id" --add "$omit_id" --budget-extra "$omit_tok")
assert_contains "expand: promotes a known omitted candidate by id" "$expand_out" "promoted: $omit_id"

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

bad_lines=$(CS expand "$slice_id" --file interp/core.py --lines abc 2>&1)
assert_contains "expand: malformed --lines reported cleanly" "$bad_lines" "--lines must look like A:B"
assert_not_contains "expand: malformed --lines, no traceback" "$bad_lines" "Traceback"

outside=$(CS expand "$slice_id" --file ../../etc/passwd --lines 1:2 --budget-extra 999 2>&1)
assert_contains "expand: --file outside the repo is rejected" "$outside" "outside the repository"

# --- error parsing: pytest / clang / MSVC locations + error keywords -------
loc_check=$(python3 -c "
import sys
sys.path.insert(0, '$DIR/scripts')
from pr_score import parse_error_locations as p, error_identifiers as ids
t = '''interp/core.py:11: ZeroDivisionError
a.cpp:3:4: fatal error: foo.h: No such file
b.hpp:7:2: note: candidate function
c.hpp:9:10:   required from here
x.cpp(12): error C2065: 'residual': undeclared identifier
FAILED tests/test_interp.py::test_midpoint - ZeroDivisionError'''
locs = set(p(t))
for want in [('interp/core.py', 11), ('a.cpp', 3), ('b.hpp', 7), ('c.hpp', 9), ('x.cpp', 12)]:
    assert want in locs, (want, locs)
assert {'ZeroDivisionError', 'residual', 'test_midpoint'} <= set(ids(t)), ids(t)
print('OK')
" 2>&1)
assert_contains "error parsing: pytest/clang note/fatal/required-from/MSVC locations" "$loc_check" "OK"

# --- bug: pytest output via --error-file finds the source chunk ------------
cat >"$SCRATCH/pytest_out.txt" <<'EOF'
=================================== FAILURES ===================================
__________________________ test_boundary_clamped_low ___________________________

    def test_boundary_clamped_low():
>       assert interpolate(-5, 0.0, 10.0) == 0.0

tests/test_interp.py:5:
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _
>       return (x - lo) / span
E       ZeroDivisionError: division by zero

interp/core.py:11: ZeroDivisionError
=========================== short test summary info ============================
FAILED tests/test_interp.py::test_boundary_clamped_low - ZeroDivisionError
EOF
uc_pt=$(CS select --task "tests are failing" --error-file "$SCRATCH/pytest_out.txt" --json)
assert_contains "pytest --error-file: source function is required" "$(json_list required_context <<<"$uc_pt")" "interp/core.py:interpolate"
assert_contains "pytest --error-file: failing test is selected" "$(json_list relevant_tests <<<"$uc_pt")" "test_boundary_clamped_low"

# --- bug: human output prints ids; --add accepts file:start-end ------------
human=$(CS select --task "Change the convergence tolerance criterion in converge()" --budget 300)
assert_contains "human output prints chunk ids" "$human" "id=csrc/solver.cpp:converge"
hid=$(sed -n 's/^slice_id: //p' <<<"$human")
omit_line=$(sed -n '/^Omitted candidates/{n;p;}' <<<"$human")
hpp_range=$(awk '{print $1}' <<<"$omit_line")
omit_hid=$(sed 's/.*id=//' <<<"$omit_line")
range_out=$(CS expand "$hid" --add "$hpp_range" --budget-extra 500 2>&1)
assert_contains "expand --add accepts a file:start-end range" "$range_out" "promoted: $omit_hid"

# --- bug: module-level error location selects a window chunk ---------------
{
  echo "def load_settings():"
  echo "    return {}"
  for i in $(seq 1 40); do echo "SETTING_$i = $i"; done
  echo "RESULT = load_settings()['missing_key']"
} >"$REPO/interp/settings.py"
modlvl=$(CS select --task "startup crash" --budget 2000 --json \
  --error $'Traceback (most recent call last):\n  File "/x/interp/settings.py", line 43, in <module>\nKeyError: missing_key')
assert_contains "module-level error: window chunk around the line is required" "$(json_list required_context <<<"$modlvl")" "interp/settings.py:28-"

printf 'def broken(:\n    pass\n' >"$REPO/interp/broken.py"
synerr=$(CS select --task "fix it" --budget 2000 --json --error 'File "interp/broken.py", line 1')
assert_contains "syntax-error file: its chunk is required (whole file, or what tree-sitter recovered)" "$(json_list required_context <<<"$synerr")" "interp/broken.py:"

# --- bug: duplicate qualnames get distinct ids -----------------------------
cat >"$REPO/interp/dup.py" <<'EOF'
import sys
if sys.platform == "win32":
    def dup_target():
        return 1
else:
    def dup_target():
        return 2
EOF
CS index >/dev/null
dup_ids=$(python3 -c "
import sys
from pathlib import Path
sys.path.insert(0, '$DIR/scripts')
import pr_store
idx = pr_store.Index(pr_store.connect(Path('$REPO/.pruner/index.sqlite')), Path('$REPO'))
ids = list(idx.by_id)
assert len(ids) == len(set(ids)), 'duplicate ids'
print(' '.join(i for i in ids if 'dup_target' in i))
" 2>&1)
assert_contains "duplicate defs: first keeps plain id" "$dup_ids" "interp/dup.py:dup_target "
assert_contains "duplicate defs: second gets @line suffix" "$dup_ids" "interp/dup.py:dup_target@6"

# --- bug: --auto-changed / --changed from a subdirectory -------------------
if command -v git >/dev/null; then
  G() { git -C "$REPO" -c user.name=t -c user.email=t@t "$@" >/dev/null 2>&1; }
  G init -q && G add -A && G commit -qm init
  echo "# touched" >>"$REPO/interp/utils.py"
  sub_auto=$(cd "$REPO/interp" && python3 "$DIR/scripts/pruner.py" select --task "review" --auto-changed --json)
  assert_contains "auto-changed from subdir (repo=cwd): paths match index" "$sub_auto" "file was recently changed"
  sub_changed=$(cd "$REPO/interp" && python3 "$DIR/scripts/pruner.py" --repo "$REPO" select --task "review" --changed utils.py --json)
  assert_contains "--changed relative to subdir cwd is normalized to repo root" "$(json_list supporting_context <<<"$sub_changed") $(json_list required_context <<<"$sub_changed")" "interp/utils.py:clamp"

  # gitignored dir used as --repo: warn + fall back to a filesystem walk
  mkdir -p "$REPO/vendored" && echo "vendored/" >"$REPO/.gitignore"
  printf 'def v():\n    return 1\n' >"$REPO/vendored/lib.py"
  vend=$(python3 "$DIR/scripts/pruner.py" --repo "$REPO/vendored" index 2>&1)
  assert_contains "gitignored --repo: warns" "$vend" "falling back to a filesystem walk"
  assert_contains "gitignored --repo: still indexes files" "$vend" "indexed 1 file(s)"
fi

# --- selection-quality eval (recall/precision vs committed baseline) --------
eval_out=$(python3 "$DIR/tests/eval/run_eval.py" 2>&1)
eval_rc=$?
echo "$eval_out" | tail -3
if [ "$eval_rc" -eq 0 ]; then
  echo "PASS: eval: recall at or above tests/eval/baseline.json"
else
  echo "FAIL: eval regression (run tests/eval/run_eval.py -v)"
  fail=1
fi

echo
if [ "$fail" -eq 0 ]; then
  echo "ALL CHECKS PASSED"
else
  echo "SOME CHECKS FAILED"
fi
exit "$fail"
