#!/usr/bin/env bash
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
# deterministic session ids: don't inherit the calling Claude Code session
unset CLAUDE_SESSION_ID CLAUDE_CODE_SESSION_ID
CC() { python3 "$DIR/scripts/seedbank.py" "$@"; }
dkey() { python3 -c "import sys; sys.path.insert(0, sys.argv[1]); from seedbank import declared_key; print(declared_key(sys.argv[2], sys.argv[3]))" "$DIR/scripts" "$1" "$2"; }

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
CC --session uc1-a observe read README.md --task "find build command" --scope build >/dev/null
CC --session uc1-b observe read README.md --task "find build command" --scope build >/dev/null
out=$(CC --session uc1-c observe read README.md --task "find build command" --scope build)
assert_contains "UC1: read access_count reaches 3" "$out" "access_count=3"
assert_contains "UC1: three distinct sessions counted" "$out" "sessions=3"

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

mistake_key=$(dkey mistake "Do not edit src/generated/**; regenerate with tools/codegen.py.")
CC promote "$mistake_key" --tier hot --critical >/dev/null

tol_key=$(dkey fact "Never weaken convergence tolerances to make tests pass; tolerance is part of the numerical accuracy contract.")
CC promote "$tol_key" --tier hot --critical >/dev/null

status=$(CC status)
assert_contains "status: 3 active hot facts after promotion" "$status" "active facts:        3  (hot=3, warm=0, stale=0)"

# --- compile: AGENTS.md carries all three, matches spec's worked examples ---
printf '# Team notes\nHand-written: ask @alice before touching CI.\n' > AGENTS.md
CC compile >/dev/null
CC compile >/dev/null
agents="$(cat AGENTS.md)"
assert_contains "compile: hand-written AGENTS.md text preserved" "$agents" "Hand-written: ask @alice before touching CI."
assert_eq "compile: exactly one seedbank block after two compiles" "$(grep -c 'seedbank:begin' AGENTS.md)" "1"
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

CC compile >/dev/null 2>&1
agents_after="$(cat AGENTS.md)"
assert_contains "compile: stale --critical fact kept, marked for verification" "$agents_after" "regenerate with tools/codegen.py. (source changed"
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
release_key=$(dkey fact "release process: tag vX.Y.Z then run tools/release.sh")
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

# --- fresh clone: empty store must not wipe a populated block ---------------
before_agents="$(cat AGENTS.md)"
CC --store "$SCRATCH/empty_store" compile >/dev/null 2>&1
rc=$?
if [ "$rc" -ne 0 ] && [ "$(cat AGENTS.md)" = "$before_agents" ]; then
  echo "PASS: compile: empty store refuses to wipe populated block (rc=$rc)"
else
  echo "FAIL: compile: empty store rc=$rc or AGENTS.md changed"
  fail=1
fi
cp AGENTS.md FORCED.md
CC --store "$SCRATCH/empty_store" compile --force --targets FORCED.md >/dev/null 2>&1
assert_not_contains "compile --force: overrides the empty-store guard" "$(cat FORCED.md)" "cmake -S . -B build"
assert_contains "compile --force: still preserves hand-written text" "$(cat FORCED.md)" "Hand-written: ask @alice"
CC --store "$SCRATCH/empty_store" import AGENTS.md >/dev/null
reimport=$(CC --store "$SCRATCH/empty_store" candidates --all --json)
assert_contains "import own output: '[scope]' suffix becomes scope" "$reimport" '"representation": "Build: cmake -S . -B build && cmake --build build -j"'
assert_not_contains "import own output: generated headings not used as scopes" "$reimport" '"scope": "hot-context'
assert_not_contains "import own output: warm pointer lines skipped" "$reimport" ".seedbank/warm/"
assert_not_contains "import own output: stale marker stripped" "$reimport" "(source changed"

# --- concurrency: parallel observes keep JSON valid and counts exact --------
for i in $(seq 1 60); do CC observe search "parallel-pattern" --scope build >/dev/null & done
wait
par_count=$(python3 -c "import json; print(json.load(open('.seedbank/keystats.json'))['search:parallel-pattern']['access_count'])" 2>&1)
assert_eq "concurrency: 60 parallel observes -> valid JSON, access_count=60" "$par_count" "60"

# --- scope sanitization: no path traversal, no crash on slashes --------------
CC observe fact "traversal probe" --scope ../../pwned >/dev/null
CC observe fact "slash probe" --scope a/b >/dev/null
trav_key=$(dkey fact "traversal probe")
slash_key=$(dkey fact "slash probe")
CC promote "$trav_key" --tier warm >/dev/null
CC promote "$slash_key" --tier warm --scope a/b >/dev/null
CC compile >/dev/null 2>&1
rc=$?
if [ "$rc" -eq 0 ] && [ ! -e "$SCRATCH/pwned.md" ] && [ ! -e "$REPO/pwned.md" ] && [ -f .seedbank/warm/a-b.md ] && [ -f .seedbank/warm/pwned.md ]; then
  echo "PASS: scope: '../../pwned' and 'a/b' sanitized to safe warm filenames"
else
  echo "FAIL: scope sanitization (rc=$rc): $(ls .seedbank/warm "$SCRATCH")"
  fail=1
fi

# --- key normalisation: trivial variants share a key; legacy keys still used -
CC observe fact "Use ninja for builds." --scope build >/dev/null
norm_out=$(CC observe fact "  use NINJA for   builds " --scope build)
assert_contains "normalise: case/whitespace/trailing-punct variants share a key" "$norm_out" "access_count=2"
ninja_key=$(dkey fact "Use ninja for builds.")
assert_contains "normalise: original representation kept for display" "$(CC candidates --all --json)" '"representation": "use NINJA for   builds"'
legacy_key="fact:$(python3 -c "import hashlib; print(hashlib.sha1(b'Legacy Fact.').hexdigest()[:12])")"
python3 -c "
import json; p='.seedbank/keystats.json'; d=json.load(open(p))
d['$legacy_key']=dict(d['$ninja_key'], access_count=1); json.dump(d, open(p,'w'))"
assert_contains "normalise: pre-existing legacy (raw-hash) key keeps being used" "$(CC observe fact "Legacy Fact.")" "$legacy_key (access_count=2,"

# --- promote: a key with an existing fact is refused, --force replaces ------
CC promote "$ninja_key" --tier warm >/dev/null
CC promote "$ninja_key" --tier warm >/dev/null 2>&1
rc=$?
count_key() { python3 -c "import json; print(sum(f['key']=='$1' for f in json.load(open('.seedbank/facts.json'))['facts'].values()))"; }
if [ "$rc" -ne 0 ] && [ "$(count_key "$ninja_key")" = "1" ]; then
  echo "PASS: promote: re-promoting an already-promoted key is refused (rc=$rc), no duplicate"
else
  echo "FAIL: promote duplicate (rc=$rc, facts for key=$(count_key "$ninja_key"))"
  fail=1
fi
CC promote "$ninja_key" --tier warm --force --representation "Build with ninja" >/dev/null
assert_eq "promote --force: replaces instead of duplicating" "$(count_key "$ninja_key")" "1"

# --- candidates: keys with a fact (incl. stale) are not re-listed ------------
echo "// regenerated again" >> src/generated/bindings.cpp
CC invalidate >/dev/null
cand_after=$(CC candidates --all --json)
assert_not_contains "candidates: promoted key not listed" "$cand_after" "\"$ninja_key\""
assert_not_contains "candidates: stale fact's key not listed" "$cand_after" "\"$mistake_key\""

# --- promote hashes sources at promote time, not at last observe -------------
CC observe fact "codegen emits bindings" --source tools/codegen.py >/dev/null
echo "# edited after observe" >> tools/codegen.py
CC promote "$(dkey fact "codegen emits bindings")" --tier warm >/dev/null
assert_not_contains "promote: source hashed at promote time (no false STALE)" "$(CC invalidate)" "source changed: tools/codegen.py"

# --- observe read of a missing path: error, nothing recorded -----------------
CC observe read does/not/exist.txt >/dev/null 2>&1
rc=$?
has_key=$(python3 -c "import json; print('read:does/not/exist.txt' in json.load(open('.seedbank/keystats.json')))")
if [ "$rc" -ne 0 ] && [ "$has_key" = "False" ]; then
  echo "PASS: observe read: missing path -> rc=$rc, nothing recorded"
else
  echo "FAIL: observe read missing path (rc=$rc, recorded=$has_key)"
  fail=1
fi

# --- observe run --timeout kills and records a failure -----------------------
to_out=$(CC observe run --timeout 1 -- sleep 5 2>&1)
rc=$?
if [ "$rc" -ne 0 ] && grep -q "timed out" <<<"$to_out"; then
  echo "PASS: observe run --timeout: killed and recorded as failed"
else
  echo "FAIL: observe run --timeout (rc=$rc): $to_out"
  fail=1
fi

# --- scoring fixtures (separate store so rankings are isolated) -------------
SC() { CC --store "$SCRATCH/score_store" "$@"; }
val() { SC candidates --all --json | python3 -c "import json,sys; print([c['value'] for c in json.load(sys.stdin) if c['key']==sys.argv[1]][0])" "$1"; }
gt() { python3 -c "import sys; sys.exit(0 if float(sys.argv[1]) > float(sys.argv[2]) else 1)" "$1" "$2"; }
check_gt() {
  if gt "$2" "$3"; then echo "PASS: $1 ($2 > $3)"; else echo "FAIL: $1 (expected $2 > $3)"; fail=1; fi
}
python3 -c "print('x' * 40000)" > big.txt   # ~10k tokens
for s in 1 2 3 4 5; do SC --session "p$s" observe read README.md >/dev/null; done
SC --session q1 observe read big.txt >/dev/null
check_gt "scoring: small file rediscovered in 5 sessions beats one read of a 10k-token file" \
  "$(val read:README.md)" "$(val read:big.txt)"
SC --session same observe read README.md >/dev/null
SC --session same observe read README.md >/dev/null
same_sess=$(SC candidates --all --json | python3 -c "import json,sys; print([c['distinct_sessions'] for c in json.load(sys.stdin) if c['key']=='read:README.md'][0])")
assert_eq "scoring: repeats within one session count once" "$same_sess" "6"
for i in $(seq 1 10); do SC observe mistake "Do not hand-edit the lockfile; run tools/lock.sh instead" >/dev/null; done
SC observe mistake "Do not hand-edit the schema file; run tools/gen.sh instead" >/dev/null
check_gt "scoring: 10x repeated mistake beats 1x (failure cost summed)" \
  "$(val "$(dkey mistake "Do not hand-edit the lockfile; run tools/lock.sh instead")")" \
  "$(val "$(dkey mistake "Do not hand-edit the schema file; run tools/gen.sh instead")")"
decay_fact="Keep solver tolerances at 1e-8 or tighter in every regression test; looser values hide real bugs."
SC observe fact "$decay_fact" --scope invariants >/dev/null
dk=$(dkey fact "$decay_fact")
age() { python3 -c "
import json, sys, time; p=sys.argv[1]; d=json.load(open(p))
d[sys.argv[2]]['last_access'] = time.time() - float(sys.argv[3]) * 86400; json.dump(d, open(p, 'w'))" \
  "$SCRATCH/score_store/keystats.json" "$dk" "$1"; }
check_gt "decay: fresh declared fact has a nonzero value above threshold 1.0" "$(val "$dk")" "1.0"
age 30
check_gt "decay: still above threshold after one half-life" "$(val "$dk")" "1.0"
age 60
check_gt "decay: below threshold after two half-lives (30d default)" "1.0" "$(val "$dk")"
assert_not_contains "decay: stale fact drops out of default candidates" "$(SC candidates --json)" "\"$dk\""
crit=$(python3 -c "import sys, time; sys.path.insert(0, sys.argv[1]); from sb_scoring import decay; print(decay(time.time() - 365 * 86400, 30, critical=True))" "$DIR/scripts")
assert_eq "decay: critical facts never decay" "$crit" "1.0"
# legacy (pre-session) keystats entry: still loads, declared fact gets a base value, migrates on next observe
python3 -c "
import json, sys, time; p=sys.argv[1]; d=json.load(open(p))
d['fact:legacy00000'] = {'kind': 'fact', 'scope': 'general', 'representation': 'legacy declared fact',
  'sources': [], 'source_hashes': {}, 'hash_changes': 0, 'access_count': 1, 'total_retrieval_cost': 0,
  'total_failure_cost': 0, 'first_seen': time.time(), 'last_access': time.time()}
json.dump(d, open(p, 'w'))" "$SCRATCH/score_store/keystats.json"
check_gt "migration: legacy declared fact (no session data) scores > 0" "$(val fact:legacy00000)" "0"
mig=$(SC --session m1 observe fact "legacy declared fact" --key fact:legacy00000)
assert_contains "migration: legacy key gains session tracking on next observe" "$mig" "sessions=2"

# --- contradictions at promote -----------------------------------------------
SC observe fact "Use tabs for indentation" --scope style >/dev/null
SC promote "$(dkey fact "Use tabs for indentation")" --tier warm >/dev/null
tabs_id=$(python3 -c "import json,sys; print([i for i,f in json.load(open(sys.argv[1]))['facts'].items() if f['representation']=='Use tabs for indentation'][0])" "$SCRATCH/score_store/facts.json")
SC observe fact "Never use tabs for indentation" --scope style >/dev/null
never_key=$(dkey fact "Never use tabs for indentation")
conflict_out=$(SC promote "$never_key" --tier warm 2>&1)
rc=$?
if [ "$rc" -ne 0 ] && grep -qF "$tabs_id" <<<"$conflict_out" && grep -qF "Use tabs for indentation" <<<"$conflict_out"; then
  echo "PASS: conflict: 'Never use tabs' refused, names $tabs_id and its text"
else
  echo "FAIL: conflict refusal (rc=$rc): $conflict_out"
  fail=1
fi
SC observe fact "Never commit build artifacts to git" --scope style >/dev/null
out=$(SC promote "$(dkey fact "Never commit build artifacts to git")" --tier warm 2>&1)
assert_contains "conflict: unrelated opposite-polarity fact in same scope allowed" "$out" "promoted"
out=$(SC promote "$never_key" --tier warm --replace "$tabs_id" 2>&1)
facts_now=$(cat "$SCRATCH/score_store/facts.json")
assert_contains "conflict --replace: new fact promoted" "$out" "promoted"
assert_not_contains "conflict --replace: old fact retired" "$facts_now" "\"$tabs_id\""
SC observe fact "Always use tabs for indentation" --scope style >/dev/null
SC promote "$(dkey fact "Always use tabs for indentation")" --tier warm >/dev/null 2>&1
rc=$?
out=$(SC promote "$(dkey fact "Always use tabs for indentation")" --tier warm --force 2>&1)
if [ "$rc" -ne 0 ] && grep -q promoted <<<"$out"; then
  echo "PASS: conflict --force: keeps both contradicting facts"
else
  echo "FAIL: conflict --force (first rc=$rc): $out"
  fail=1
fi

# --- Claude Code PostToolUse hook --------------------------------------------
git init -q . 2>/dev/null
HOOK="$DIR/bin/seedbank"
hook() { printf '%s' "$1" | "$HOOK" hook; }
reads_before=$(python3 -c "import json; print(json.load(open('.seedbank/keystats.json'))['read:README.md']['access_count'])")
out1=$(hook "{\"session_id\":\"hk1\",\"cwd\":\"$REPO/src\",\"tool_name\":\"Read\",\"tool_input\":{\"file_path\":\"../README.md\"}}")
rc1=$?
out2=$(hook "{\"session_id\":\"hk2\",\"cwd\":\"$REPO\",\"tool_name\":\"Read\",\"tool_input\":{\"file_path\":\"./README.md\"}}")
hook "{\"session_id\":\"hk3\",\"cwd\":\"$REPO\",\"tool_name\":\"Read\",\"tool_input\":{\"file_path\":\"$REPO/README.md\"}}"
hook "{\"session_id\":\"hk3\",\"cwd\":\"$REPO\",\"tool_name\":\"Read\",\"tool_input\":{\"file_path\":\"$SCRATCH/outside.txt\"}}"
echo outside > "$SCRATCH/outside.txt"
hook "{\"session_id\":\"hk3\",\"cwd\":\"$REPO\",\"tool_name\":\"Read\",\"tool_input\":{\"file_path\":\"$SCRATCH/outside.txt\"}}"
hook "{\"session_id\":\"hk3\",\"cwd\":\"$REPO\",\"tool_name\":\"Read\",\"tool_input\":{\"file_path\":\"$REPO/.seedbank/facts.json\"}}"
hook "{\"session_id\":\"hk3\",\"cwd\":\"$REPO\",\"tool_name\":\"Grep\",\"tool_input\":{\"pattern\":\"codegen\",\"path\":\"$REPO/tools\",\"glob\":\"*.py\"}}"
hook "{\"session_id\":\"hk3\",\"cwd\":\"$REPO\",\"tool_name\":\"Glob\",\"tool_input\":{\"pattern\":\"**/*.cpp\"}}"
ks=$(cat .seedbank/keystats.json)
reads_after=$(python3 -c "import json; print(json.load(open('.seedbank/keystats.json'))['read:README.md']['access_count'])")
assert_eq "hook: Read via ../, ./ and absolute paths all normalise to read:README.md" "$reads_after" "$((reads_before + 3))"
assert_eq "hook: exit 0 and no stdout" "$rc1|$out1$out2" "0|"
assert_contains "hook: hook session_id recorded" "$ks" '"hk2"'
assert_contains "hook: Grep recorded as search with repo-relative path + glob" "$ks" '"search:codegen path=tools glob=*.py"'
assert_contains "hook: Glob recorded as search" "$ks" '"search:**/*.cpp"'
assert_not_contains "hook: file outside the repo skipped" "$ks" "outside.txt"
assert_not_contains "hook: the store itself is skipped" "$ks" "read:.seedbank"
before_sum=$(cksum < .seedbank/keystats.json)
out=$(hook 'not json {')
rc=$?
out2=$(hook '["a list"]')
assert_eq "hook: malformed JSON -> exit 0, no stdout" "$rc|$out$out2" "0|"
assert_eq "hook: malformed JSON -> nothing recorded" "$(cksum < .seedbank/keystats.json)" "$before_sum"
mkdir -p "$SCRATCH/nogit" && cd "$SCRATCH/nogit"
hook "{\"session_id\":\"x\",\"cwd\":\"$SCRATCH/nogit\",\"tool_name\":\"Glob\",\"tool_input\":{\"pattern\":\"*\"}}"
if [ ! -e "$SCRATCH/nogit/.seedbank" ]; then echo "PASS: hook: outside a git repo -> no store created"; else echo "FAIL: hook created a store outside a git repo"; fail=1; fi
cd "$REPO"

echo
if [ "$fail" -eq 0 ]; then
  echo "ALL CHECKS PASSED"
else
  echo "SOME CHECKS FAILED"
fi
exit "$fail"
