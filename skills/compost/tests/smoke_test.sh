#!/usr/bin/env bash
# Exercises every command against the bundled fixtures and asserts on
# observable behavior: clustering, profile detection, delta mode,
# retrieval, and lossless recoverability. No network, no LLM calls.
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIX="$DIR/tests/fixtures"
STORE="$(mktemp -d)"
trap 'rm -rf "$STORE"' EXIT

PY="${PYTHON:-python3}"  # e.g. PYTHON=/usr/bin/python3 to test another interpreter
tf() { "$PY" "$DIR/scripts/compost.py" --store "$STORE" "$@"; }

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

assert_status() {
  local desc="$1" got="$2" want="$3"
  if [ "$got" -eq "$want" ]; then
    echo "PASS: $desc"
  else
    echo "FAIL: $desc (exit $got, expected $want)"
    fail=1
  fi
}

# --- pytest: 27+8+2 failure clustering -----------------------------------
out_pytest=$(tf ingest --file "$FIX/pytest_fail.log" --command "pytest -q" --exit-code 1)
assert_contains "pytest: root cause is the matrix-dimensions error" "$out_pytest" "incompatible matrix dimensions"
assert_contains "pytest: root cause count is 27" "$out_pytest" "x27"
assert_contains "pytest: secondary group init x8" "$out_pytest" "solver not initialized x8"
assert_contains "pytest: secondary group teardown x2" "$out_pytest" "teardown handle already closed x2"
assert_contains "pytest: status FAIL" "$out_pytest" "status=FAIL"

# --- gcc: 60 system-header template errors collapse; root is the user file --
out_gcc=$(tf ingest --file "$FIX/gcc_errors.log" --command "make" --exit-code 2)
assert_contains "gcc: repeated errors collapse to one group" "$out_gcc" "no match for 'operator<'"
assert_contains "gcc: collapsed count is 60" "$out_gcc" "x60"
assert_contains "gcc: profile auto-detected" "$out_gcc" "profile=gcc"
assert_contains "gcc: template spam shows user-code call site" "$out_gcc" "from user code at src/main.cpp:14:14"
root_gcc=$(sed -n '/^Root error/{n;p;}' <<<"$out_gcc")
assert_contains "gcc: root is first error in a user file (not the x60 header group)" "$root_gcc" "src/solver.cpp:42: 'tol' was not declared"

# --- slurm: must not be misdetected as gcc, no double counting -------------
out_slurm=$(tf ingest --file "$FIX/slurm_oom.log" --command "srun ./sim" --exit-code 137)
assert_contains "slurm: profile auto-detected (not gcc)" "$out_slurm" "profile=slurm"
assert_contains "slurm: OOM termination reason extracted" "$out_slurm" "termination_reason: out_of_memory"
assert_contains "slurm: peak memory extracted" "$out_slurm" "peak_memory: 64200000K"
assert_not_contains "slurm: no double-counted event (x2 on a single-occurrence line)" "$out_slurm" "x2"

# --- clean run: no false positives -----------------------------------------
out_clean=$(tf ingest --file "$FIX/clean_pass.log" --command "pytest -q clean" --exit-code 0)
assert_contains "clean run: status PASS" "$out_clean" "status=PASS"
assert_contains "clean run: no errors detected" "$out_clean" "No errors detected."

# --- numerical solver delta mode --------------------------------------------
out_r1=$(tf ingest --file "$FIX/solver_run1.log" --command "./solve --tol 1e-6" --exit-code 1)
run1_id=$(sed -n 's/.*raw_output_id=\(r[0-9]*\).*/\1/p' <<<"$out_r1")
assert_contains "solver run1: WARNING line captured despite numerical profile" "$out_r1" "slow convergence detected"

out_r2=$(tf ingest --file "$FIX/solver_run2.log" --command "./solve --tol 1e-6" --exit-code 0)
assert_contains "solver run2: delta reports improved residual" "$out_r2" "0.00012 -> 8.7e-07"
assert_contains "solver run2: delta reports resolved warning" "$out_r2" "Resolved:"

# --- lossless recoverability -------------------------------------------------
recovered="$(tf get "$run1_id" --lines all | sed -E 's/^[0-9]+: //')"
original="$(cat "$FIX/solver_run1.log")"
if [ "$recovered" = "$original" ]; then
  echo "PASS: get --lines all losslessly recovers the original fixture"
else
  echo "FAIL: get --lines all losslessly recovers the original fixture"
  fail=1
fi

get_capped_count=$(tf get "$run1_id" --lines 1:10000 2>/dev/null | wc -l | tr -d ' ')
if [ "$get_capped_count" -le 400 ]; then
  echo "PASS: get is capped at MAX_GET_LINES per call"
else
  echo "FAIL: get is capped at MAX_GET_LINES per call (got $get_capped_count lines)"
  fail=1
fi

# --- clean error handling (no raw tracebacks) --------------------------------
event_err=$(tf event "$run1_id" --event 999 2>&1)
assert_not_contains "event: unknown id fails cleanly (no Python traceback)" "$event_err" "Traceback (most recent call last)"

get_err=$(tf get r9999 --lines 1:5 2>&1)
assert_not_contains "get: unknown run id fails cleanly (no Python traceback)" "$get_err" "Traceback (most recent call last)"

# --- live run: exit code propagation -----------------------------------------
tf run -- python3 -c "import sys; sys.exit(7)" >/dev/null 2>&1
assert_status "live run: wrapped command's exit code is propagated" "$?" 7

# --- ANSI colour escapes are stripped before parsing -------------------------
out_ansi=$(tf ingest --file "$FIX/ansi_clang.log" --command "clang++ -fcolor-diagnostics x.cpp" --exit-code 1)
assert_contains "ansi: coloured clang error extracted" "$out_ansi" "use of undeclared identifier 'foo'"
ansi_root=$(tf ingest --file "$FIX/ansi_clang.log" --command "clang++ root" --exit-code 1 --json \
  | "$PY" -c "import json,sys; d=json.load(sys.stdin); print(d['root_events'][0]['message']); print([e.get('user_location') for e in d['repeated_events']])")
assert_contains "root: user file wins over earlier libc++ sort.h error" "$ansi_root" "src/app/main.cpp:12: use of undeclared identifier 'foo'"
assert_contains "root: sort.h error keeps its user-code instantiation site" "$ansi_root" "src/app/main.cpp:9:8"
assert_not_contains "ansi: no escape bytes in summary" "$out_ansi" $'\033'
ansi_id=$(sed -n 's/.*raw_output_id=\(r[0-9]*\).*/\1/p' <<<"$out_ansi")
if cmp -s <(tf get "$ansi_id" --lines all | sed -E 's/^[0-9]+: //') "$FIX/ansi_clang.log"; then
  echo "PASS: ansi: raw output stored verbatim (escapes kept)"
else
  echo "FAIL: ansi: raw output stored verbatim (escapes kept)"
  fail=1
fi

# --- cmake --build / ninja: compiler + linker errors not dropped --------------
out_ninja=$(tf ingest --file "$FIX/ninja_cmake_build.log" --command "cmake --build build" --exit-code 1)
assert_contains "cmake --build: clang error extracted" "$out_ninja" "no member named 'solve' in 'Grid'"
assert_contains "cmake --build: linker error extracted" "$out_ninja" "Undefined symbols for architecture"

# --- FAIL with no extracted events falls back to raw tail ----------------------
out_opaque=$(tf ingest --file "$FIX/opaque_fail.log" --command "./sim" --exit-code 3)
assert_contains "opaque failure: raw tail included" "$out_opaque" "invariant violated in cell 4411"

# --- pytest: message choice, failing test names, collection errors -------------
out_pa=$(tf ingest --file "$FIX/pytest_assert.log" --command "pytest tests/" --exit-code 1)
assert_contains "pytest: message from short summary" "$out_pa" "AssertionError: assert ['a', 'b'] == ['a', 'b', 'c']"
assert_not_contains "pytest: no diff-hint as message" "$out_pa" "Use -v to get more diff"
assert_contains "pytest: bare FAILED uses first E line" "$out_pa" "assert 1 == 2"
assert_contains "pytest: collection error shows exception" "$out_pa" "ModuleNotFoundError: No module named 'missing_mod'"
assert_contains "pytest: failing test names listed" "$out_pa" "tests/test_io.py::test_write"
assert_contains "pytest: +N more for many failing tests" "$out_pytest" "Failing tests (37):"
assert_contains "pytest: failing tests list capped" "$out_pytest" "(+27 more)"

# --- diff reports which tests newly fail / pass ---------------------------------
tf ingest --file "$FIX/pytest_diff_1.log" --command "pytest -k diff" --exit-code 1 >/dev/null
out_d2=$(tf ingest --file "$FIX/pytest_diff_2.log" --command "pytest -k diff" --exit-code 1)
assert_contains "diff: newly failing test reported" "$out_d2" "+ tests/test_k.py::test_d"
assert_contains "diff: newly passing test reported" "$out_d2" "- tests/test_k.py::test_a"
assert_not_contains "diff: not reported as unchanged" "$out_d2" "No structural changes."

# --- capped groups are announced, not silently dropped --------------------------
out_many=$(tf run -- python3 -c "
import string
for c in string.ascii_lowercase[:15]: print('ERROR: failure kind ' + c * 3)
for i in range(30): print('progress', i)
raise SystemExit(1)")
assert_contains "truncation: omitted groups announced" "$out_many" "(+4 more groups, see"

# --- concurrency: 20 parallel runs keep the store valid, ids unique -------------
PSTORE="$(mktemp -d)"
for i in $(seq 1 20); do
  "$PY" "$DIR/scripts/compost.py" --store "$PSTORE" run -- python3 -c "print('run $i')" >/dev/null 2>&1 &
done
wait
if "$PY" - "$PSTORE" <<'PY'
import json, sys, pathlib
root = pathlib.Path(sys.argv[1])
idx = json.loads((root / "index.json").read_text())
ids = [r for runs in idx["by_signature"].values() for r in runs]
metas = list((root / "runs").glob("*/meta.json"))
assert idx["seq"] == 20 and len(ids) == 20 and len(set(ids)) == 20 and len(metas) == 20, (idx["seq"], len(ids), len(metas))
PY
then
  echo "PASS: concurrency: 20 parallel runs -> valid index, 20 unique run ids"
else
  echo "FAIL: concurrency: 20 parallel runs -> valid index, 20 unique run ids"
  fail=1
fi
rm -rf "$PSTORE"

# --- run ids are validated; reads never create directories ----------------------
tf get ../../pwned --lines 1:2 >/dev/null 2>&1
assert_status "get: path-traversal run id rejected" "$?" 1
if [ -e "$STORE/../pwned" ] || [ -e "$STORE/runs/../../pwned" ]; then
  echo "FAIL: get: traversal id created no directory"
  fail=1
else
  echo "PASS: get: traversal id created no directory"
fi

# --- exit codes: missing command 127, signal 128+N ------------------------------
tf run -- compost-definitely-missing-cmd >/dev/null 2>&1
assert_status "live run: missing command exits 127" "$?" 127
tf run -- python3 -c "import os, signal; os.kill(os.getpid(), signal.SIGTERM)" >/dev/null 2>&1
assert_status "live run: signal-killed command exits 128+SIGTERM" "$?" 143

# --- pytest: counts come from the final summary line, not captured output -------
out_pc=$(printf '%s\n' 'tests/test_x.py .F.' 'captured: 99 passed earlier' '=== 1 failed, 3 passed in 0.52s ===' \
  | tf ingest --stdin --command "pytest counts" --exit-code 1)
assert_contains "pytest: passed count from final summary line" "$out_pc" "tests_passed: 3"
assert_not_contains "pytest: captured '99 passed' ignored" "$out_pc" "tests_passed: 99"

# --- clang/gcc linker driver failure lines are linker events -------------------
out_ld=$(printf '%s\n' 'Linking CXX executable app' 'clang++: error: linker command failed with exit code 1 (use -v to see invocation)' \
  | tf ingest --stdin --command "link" --profile cmake --exit-code 1 --json)
assert_contains "linker: clang 'linker command failed' extracted" "$out_ld" '"message": "clang++: error: linker command failed'
out_ld2=$(printf '%s\n' 'collect2: error: ld returned 1 exit status' \
  | tf ingest --stdin --command "link2" --profile cmake --exit-code 1 --json)
assert_contains "linker: collect2 ld failure extracted" "$out_ld2" '"message": "collect2: error: ld returned 1 exit status"'

# --- weak signals never FAIL an exit-0 run -------------------------------------
out_z=$(printf '%s\n' 'ran 12 cases: 12 ok, 0 failed' | tf ingest --stdin --command "./check" --exit-code 0)
assert_contains "generic: '0 failed' on exit 0 is PASS" "$out_z" "status=PASS"
out_inf=$(printf '%s\n' 'iter 1 residual 1e-2' 'iter 2 residual 1e-5' 'final inf-norm error 3e-6' \
  | tf ingest --stdin --command "./solve inf" --profile numerical_solver --exit-code 0)
assert_contains "solver: 'inf-norm' is not a nan/inf" "$out_inf" "nan_or_inf_detected: False"
assert_contains "solver: 'inf-norm' run is PASS" "$out_inf" "status=PASS"
out_nan=$(printf '%s\n' 'iter 1 residual 1e-2' 'value = nan' | tf ingest --stdin --command "./solve nan" --profile numerical_solver --exit-code 0)
assert_contains "solver: standalone nan token still detected" "$out_nan" "nan_or_inf_detected: True"

# --- child stdin is /dev/null; --timeout kills the process group ----------------
out_in=$(tf run --timeout 10 -- "$PY" -c "import sys; print('stdin=%r' % sys.stdin.read())")
assert_contains "run: child stdin is /dev/null" "$out_in" "stdin=''"
out_to=$(tf run --timeout 1 -- "$PY" -c "import time; print('started', flush=True); time.sleep(30)")
st=$?
assert_status "run --timeout: exits 124" "$st" 124
assert_contains "run --timeout: status TIMEOUT" "$out_to" "status=TIMEOUT"
assert_contains "run --timeout: partial output kept" "$out_to" "started"

# --- get single line / bad range, grep bad regex / -F --------------------------
assert_contains "get --lines N: single line" "$(tf get "$run1_id" --lines 5)" "5: "
bad_get=$(tf get "$run1_id" --lines abc 2>&1); st=$?
assert_status "get: bad --lines exits 2" "$st" 2
assert_not_contains "get: bad --lines no traceback" "$bad_get" "Traceback"
bad_grep=$(tf grep "$run1_id" '(' 2>&1); st=$?
assert_status "grep: invalid regex exits 2" "$st" 2
assert_not_contains "grep: invalid regex no traceback" "$bad_grep" "Traceback"
paren_id=$(tf run -- "$PY" -c "print('call f(x')" | sed -n 's/.*raw_output_id=\(r[0-9]*\).*/\1/p')
assert_contains "grep -F: literal pattern" "$(tf grep "$paren_id" -F 'f(x')" "call f(x"

# --- small output passes through raw; large output is summarized ----------------
out_small=$(tf run -- "$PY" -c "print('hello small')")
assert_contains "small output: raw passthrough" "$out_small" "  hello small"
assert_not_contains "small output: no retrieval boilerplate" "$out_small" "Retrieval:"
out_big=$(tf run -- "$PY" -c "
for i in range(40): print('line', i)")
assert_contains "large output: summarized" "$out_big" "Retrieval:"
assert_not_contains "large output: raw not dumped" "$out_big" "line 39"

# --- clustering keeps identity: small ints are not volatile ----------------------
out_id=$(printf '%s\n' \
  '=========================== short test summary info ============================' \
  'FAILED tests/t.py::test_a - assert 3 == 2' \
  'FAILED tests/t.py::test_b - assert 1 == 0' \
  'FAILED tests/t.py::test_c - Timeout at 0x7ffee4a1 after 1234567 us' \
  'FAILED tests/t.py::test_d - Timeout at 0x7ffee9ff after 7654321 us' \
  | tf ingest --stdin --command "pytest ident" --exit-code 1 --json)
n_groups=$("$PY" -c "import json,sys; d=json.load(sys.stdin); print(len(d['root_events'])+len(d['repeated_events']))" <<<"$out_id")
if [ "$n_groups" = "3" ]; then
  echo "PASS: cluster: 'assert 3 == 2' / 'assert 1 == 0' stay separate; hex/long-number variants merge"
else
  echo "FAIL: cluster: expected 3 groups, got $n_groups"
  fail=1
fi

# --- run: SIGTERM mid-run keeps partial output and records the signal -----------
KSTORE="$(mktemp -d)"
"$PY" "$DIR/scripts/compost.py" --store "$KSTORE" run -- bash -c 'echo start; sleep 30' >"$KSTORE/out.txt" 2>&1 &
kpid=$!
for _ in $(seq 1 50); do
  grep -qs start "$KSTORE"/runs/*/raw.txt && break
  sleep 0.1
done
kill -TERM "$kpid"
wait "$kpid"
assert_status "run killed: exits 128+SIGTERM" "$?" 143
assert_contains "run killed: raw.txt has partial output" "$(cat "$KSTORE"/runs/*/raw.txt)" "start"
kmeta=$(cat "$KSTORE"/runs/*/meta.json)
assert_contains "run killed: meta records the signal" "$kmeta" '"signal": "SIGTERM"'
assert_contains "run killed: meta status killed" "$kmeta" '"status": "killed"'
assert_contains "run killed: summary still printed" "$(cat "$KSTORE/out.txt")" "status=KILLED"
rm -rf "$KSTORE"

# --- retention: last 10 runs per command; gc keeps latest run per command -------
RSTORE="$(mktemp -d)"
rt() { "$PY" "$DIR/scripts/compost.py" --store "$RSTORE" "$@"; }
for i in $(seq 1 12); do
  rt run -- "$PY" -c "print('same')" >/dev/null 2>&1
done
rt run -- "$PY" -c "print('other')" >/dev/null 2>&1
n_runs=$(ls "$RSTORE/runs" | wc -l | tr -d ' ')
if [ "$n_runs" = "11" ] && [ ! -e "$RSTORE/runs/r0002" ] && [ -e "$RSTORE/runs/r0003" ] && [ -e "$RSTORE/runs/r0012" ]; then
  echo "PASS: retention: 12 runs of one command -> last 10 kept (+1 other command)"
else
  echo "FAIL: retention: expected r0003..r0013, got: $(ls "$RSTORE/runs" | tr '\n' ' ')"
  fail=1
fi
rt gc --keep-per-command 5 --max-store-mb 0 >/dev/null
left=$(ls "$RSTORE/runs" | tr '\n' ' ')
if [ "$left" = "r0012 r0013 " ]; then
  echo "PASS: gc: size cap never evicts the latest run of each command"
else
  echo "FAIL: gc: expected 'r0012 r0013', got '$left'"
  fail=1
fi
assert_contains "gc: diff vs previous run still works" "$(rt run -- "$PY" -c "print('same')")" "Changed since previous run (r0012)"
rm -rf "$RSTORE"

# --- 200k-line output: streamed to disk, parsed with flat memory ----------------
out_huge=$(tf run --json -- "$PY" -c "
import sys
w = sys.stdout.write
for i in range(200000):
    w('WARNING: slow step %d\n' % i if i % 1000 == 0 else 'progress step %d of 200000: ok\n' % i)
")
huge_id=$("$PY" -c "import json,sys; print(json.load(sys.stdin)['raw_output_id'])" <<<"$out_huge")
assert_contains "huge: all 200k lines counted" "$out_huge" '"raw_line_count": 200000'
peak_mb=$("$PY" -c '
import resource, sys
sys.path.insert(0, sys.argv[1])
import compost
from co_store import Store
store = Store(sys.argv[2])
before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
compost.process_and_store(store, sys.argv[3], "huge-reparse", 0, 0.0, None)
peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
scale = 1 if sys.platform == "darwin" else 1024  # bytes on macOS, KiB on Linux
print(int((peak - before) * scale / 1e6))
' "$DIR/scripts" "$STORE" "$huge_id")
if [ -n "$peak_mb" ] && [ "$peak_mb" -lt 40 ]; then
  echo "PASS: huge: parsing 200k lines grows peak RSS by ${peak_mb} MB (< 40)"
else
  echo "FAIL: huge: peak RSS growth ${peak_mb:-?} MB"
  fail=1
fi
meta_kb=$(( $(wc -c <"$STORE/runs/$huge_id/meta.json") / 1024 ))
if [ "$meta_kb" -lt 64 ]; then
  echo "PASS: huge: meta.json stays small (${meta_kb} KB)"
else
  echo "FAIL: huge: meta.json is ${meta_kb} KB"
  fail=1
fi

echo
if [ "$fail" -eq 0 ]; then
  echo "ALL CHECKS PASSED"
else
  echo "SOME CHECKS FAILED"
fi
exit "$fail"
