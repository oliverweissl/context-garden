#!/usr/bin/env bash
# Automated smoke test referenced by ../validate.md.
# Exercises every command against the bundled fixtures and asserts on
# observable behavior: clustering, profile detection, delta mode,
# retrieval, and lossless recoverability. No network, no LLM calls.
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIX="$DIR/tests/fixtures"
STORE="$(mktemp -d)"
trap 'rm -rf "$STORE"' EXIT

tf() { python3 "$DIR/scripts/compost.py" --store "$STORE" "$@"; }

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

# --- gcc: 60 template-instantiation errors collapse to 1 -------------------
out_gcc=$(tf ingest --file "$FIX/gcc_errors.log" --command "make" --exit-code 2)
assert_contains "gcc: repeated errors collapse to one group" "$out_gcc" "no matching function"
assert_contains "gcc: collapsed count is 60" "$out_gcc" "x60"
assert_contains "gcc: profile auto-detected" "$out_gcc" "profile=gcc"

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

echo
if [ "$fail" -eq 0 ]; then
  echo "ALL CHECKS PASSED"
else
  echo "SOME CHECKS FAILED"
fi
exit "$fail"
