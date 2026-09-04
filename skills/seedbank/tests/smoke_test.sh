#!/usr/bin/env bash
# Automated smoke test referenced by ../validate.md.
# Copies tests/fixtures/mini_repo to a scratch dir and drives the full
# observe -> candidates -> promote -> compile -> invalidate -> gc lifecycle,
# covering the spec's three worked use cases (build discovery, repeated
# mistake, scientific invariant). No network, no LLM calls.
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRATCH="$(mktemp -d)"
REPO="$SCRATCH/mini_repo"
cp -R "$DIR/tests/fixtures/mini_repo" "$REPO"
trap 'rm -rf "$SCRATCH"' EXIT

cd "$REPO"
CC() { python3 "$DIR/scripts/seedbank.py" "$@"; }

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
assert_eq() {
  local desc="$1" got="$2" want="$3"
  if [ "$got" = "$want" ]; then
    echo "PASS: $desc"
  else
    echo "FAIL: $desc (got '$got', want '$want')"
    fail=1
  fi
}

# --- UC1: build discovery via repeated reads --------------------------------
CC observe read README.md --task "find build command" --scope build >/dev/null
CC observe read README.md --task "find build command" --scope build >/dev/null
out=$(CC observe read README.md --task "find build command" --scope build)
assert_contains "UC1: read access_count reaches 3" "$out" "access_count=3"

# --- UC2: repeated mistake (edit-then-revert a generated file) --------------
CC observe mistake "Do not edit src/generated/**; regenerate with tools/codegen.py." \
  --scope invariants --source src/generated/bindings.cpp >/dev/null
out=$(CC observe mistake "Do not edit src/generated/**; regenerate with tools/codegen.py." \
  --scope invariants --source src/generated/bindings.cpp)
assert_contains "UC2: mistake access_count reaches 2" "$out" "access_count=2"

# --- UC3: scientific invariant, no backing source ----------------------------
CC observe fact "Never weaken convergence tolerances to make tests pass; tolerance is part of the numerical accuracy contract." \
  --scope invariants >/dev/null

# --- candidates: mistake should outrank a bare read (failure cost >> read cost)
cand=$(CC candidates --all --json)
mistake_val=$(python3 -c "import json,sys; d=json.load(sys.stdin); print([c['value'] for c in d if c['kind']=='mistake'][0])" <<<"$cand")
read_val=$(python3 -c "import json,sys; d=json.load(sys.stdin); print([c['value'] for c in d if c['kind']=='read'][0])" <<<"$cand")
if python3 -c "exit(0 if $mistake_val > $read_val else 1)"; then
  echo "PASS: candidates: repeated-mistake value ($mistake_val) outranks a plain repeated read ($read_val)"
else
  echo "FAIL: candidates: expected mistake value > read value, got $mistake_val vs $read_val"
  fail=1
fi

# --- promote all three (manual promotion; --critical for the two invariants) -
out=$(CC promote read:README.md --representation "Build: cmake -S . -B build && cmake --build build -j" --tier hot --scope build)
assert_contains "promote: build fact assigned f0001" "$out" "f0001"

mistake_key=$(python3 -c "import hashlib; print('mistake:'+hashlib.sha1(b'Do not edit src/generated/**; regenerate with tools/codegen.py.').hexdigest()[:12])")
CC promote "$mistake_key" --tier hot --critical >/dev/null

tol_key=$(python3 -c "import hashlib; print('fact:'+hashlib.sha1(b'Never weaken convergence tolerances to make tests pass; tolerance is part of the numerical accuracy contract.').hexdigest()[:12])")
CC promote "$tol_key" --tier hot --critical >/dev/null

status=$(CC status)
assert_contains "status: 3 active hot facts after promotion" "$status" "active facts:        3  (hot=3, warm=0, stale=0)"

# --- compile: AGENTS.md carries all three, matches spec's worked examples ---
CC compile >/dev/null
agents="$(cat AGENTS.md)"
assert_contains "compile: build command present verbatim" "$agents" "cmake -S . -B build && cmake --build build -j"
assert_contains "compile: generated-file invariant present verbatim" "$agents" "Do not edit src/generated/**; regenerate with tools/codegen.py."
assert_contains "compile: tolerance invariant present verbatim" "$agents" "Never weaken convergence tolerances"

token_count=$(python3 -c "print(len(open('AGENTS.md').read().split()))")
if [ "$token_count" -le 500 ]; then
  echo "PASS: compile: hot AGENTS.md word count ($token_count) within 500-token-ish default budget"
else
  echo "FAIL: compile: hot AGENTS.md word count ($token_count) exceeds default budget"
  fail=1
fi

# --- invalidation: changing the generated file's content must stale its fact
echo "// regenerated" >> src/generated/bindings.cpp
inval_out=$(CC invalidate)
assert_contains "invalidate: detects the changed source" "$inval_out" "STALE"
assert_contains "invalidate: names the changed file" "$inval_out" "src/generated/bindings.cpp"

CC compile >/dev/null
agents_after="$(cat AGENTS.md)"
assert_not_contains "compile: stale fact is excluded from AGENTS.md (no stale exposure)" "$agents_after" "Do not edit src/generated"
assert_contains "compile: unrelated hot facts remain present" "$agents_after" "cmake -S . -B build"

# --- revalidation clears staleness and restores the fact to output ----------
CC invalidate --confirm "$mistake_key" >/dev/null 2>&1 || true
# fact ids, not keys, are used for --confirm; fetch the real fact id
fact_id=$(python3 -c "
import json
d = json.load(open('.seedbank/facts.json'))
for fid, f in d['facts'].items():
    if f['key'] == '$mistake_key':
        print(fid)
")
CC invalidate --confirm "$fact_id" >/dev/null
CC compile >/dev/null
agents_revalidated="$(cat AGENTS.md)"
assert_contains "revalidate: fact restored to AGENTS.md after confirm" "$agents_revalidated" "Do not edit src/generated"

# --- stats: tokens-avoided is reported and non-negative ----------------------
stats_json=$(CC stats --json)
total_avoided=$(python3 -c "import json,sys; print(json.load(sys.stdin)['total_tokens_avoided'])" <<<"$stats_json")
if python3 -c "exit(0 if $total_avoided >= 0 else 1)"; then
  echo "PASS: stats: total_tokens_avoided is reported and non-negative ($total_avoided)"
else
  echo "FAIL: stats: total_tokens_avoided is negative ($total_avoided)"
  fail=1
fi

# --- eviction: a tight hot budget demotes the lowest-value non-critical fact
CC observe fact "release process: tag vX.Y.Z then run tools/release.sh" --scope release >/dev/null
release_key=$(python3 -c "import hashlib; print('fact:'+hashlib.sha1(b'release process: tag vX.Y.Z then run tools/release.sh').hexdigest()[:12])")
evict_out=$(CC promote "$release_key" --tier hot --budget 60)
assert_contains "eviction: non-critical build fact demoted to stay within budget" "$evict_out" "evicted (demoted to warm) to stay within hot budget"

status2=$(CC status)
assert_contains "eviction: critical facts survive (still 2 hot invariants)" "$status2" "hot=3"

# --- gc: pruning raw observations preserves aggregate access_count ----------
before_count=$(python3 -c "import json; print(json.load(open('.seedbank/keystats.json'))['read:README.md']['access_count'])")
for i in 1 2 3 4 5; do CC observe read README.md --task "loop" >/dev/null; done
CC gc --keep-per-key 2 >/dev/null
after_count=$(python3 -c "import json; print(json.load(open('.seedbank/keystats.json'))['read:README.md']['access_count'])")
obs_lines=$(wc -l < .seedbank/observations.jsonl | tr -d ' ')
assert_eq "gc: aggregate access_count preserved across pruning" "$after_count" "$((before_count + 5))"
if [ "$obs_lines" -le 20 ]; then
  echo "PASS: gc: raw observation log pruned (now $obs_lines lines)"
else
  echo "FAIL: gc: raw observation log not pruned (still $obs_lines lines)"
  fail=1
fi

# --- import: seeding candidates from an existing AGENTS.md-style file -------
cat > /tmp/tf_sb_existing_agents.$$ <<'EOF'
# CUDA
- Requires CUDA 12.x, compute capability 8.0+
EOF
import_out=$(CC import "/tmp/tf_sb_existing_agents.$$")
rm -f "/tmp/tf_sb_existing_agents.$$"
assert_contains "import: seeds one candidate from a heading+bullet file" "$import_out" "imported 1 candidate"

echo
if [ "$fail" -eq 0 ]; then
  echo "ALL CHECKS PASSED"
else
  echo "SOME CHECKS FAILED"
fi
exit "$fail"
