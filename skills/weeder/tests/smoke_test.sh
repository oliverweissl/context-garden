#!/usr/bin/env bash
# Automated smoke test referenced by ../validate.md.
# Exercises audit/optimize/test-routing/test-function/diff against
# tests/fixtures/bloated_skill, which deliberately combines all three
# spec use cases: an oversized skill (UC1), a rule repeated five times
# (UC2), and a vague routing description that loses to a real competitor
# (UC3). No network, no LLM calls.
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIX="$DIR/tests/fixtures"
SCRATCH="$(mktemp -d)"
trap 'rm -rf "$SCRATCH"' EXIT

WE() { python3 "$DIR/scripts/weeder.py" "$@"; }

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

# --- UC1: audit finds the oversized examples/background sections -----------
audit_out=$(WE audit "$FIX/bloated_skill")
assert_contains "audit: flags Background as movable" "$audit_out" "category 'background' in SKILL.md"
assert_contains "audit: flags Examples as movable" "$audit_out" "category 'examples' in SKILL.md"
assert_contains "audit: catches generic filler phrasing" "$audit_out" "generic filler phrasing"

# --- UC2: audit's duplicate detector finds the 4x-repeated rule -------------
assert_contains "audit: finds the 4x-repeated 'never delete' rule" "$audit_out" "4 occurrence(s)"
assert_contains "audit: reports the repeated rule's exact text" "$audit_out" "Never delete a data file without explicit user confirmation first."

# --- optimize: mechanical move-to-references is lossless -------------------
opt_out="$SCRATCH/bloated_skill-optimized"
WE optimize "$FIX/bloated_skill" --out "$opt_out" >/tmp/we_optimize_out.$$ 2>&1
optimize_report=$(cat /tmp/we_optimize_out.$$); rm -f /tmp/we_optimize_out.$$
assert_contains "optimize: moves Background to references/" "$optimize_report" "moved 'Background'"
assert_contains "optimize: moves Examples to references/" "$optimize_report" "moved 'Examples'"

func_out=$(WE test-function "$FIX/bloated_skill" "$opt_out")
func_exit=$?
assert_contains "optimize: mechanical move preserves all constraints (lossless)" "$func_out" "preserved in after"
if [ "$func_exit" -eq 0 ]; then
  echo "PASS: optimize: test-function exits 0 (100% preserved)"
else
  echo "FAIL: optimize: test-function exits 0 (100% preserved)"
  fail=1
fi

# --- test-function actually catches a real regression, not just the safe case
badedit="$SCRATCH/bloated_skill-badedit"
cp -R "$FIX/bloated_skill" "$badedit"
python3 -c "
path = '$badedit/SKILL.md'
text = open(path).read()
text = text.split('## Rules')[0] + '## Examples' + text.split('## Examples', 1)[1]
open(path, 'w').write(text)
"
bad_out=$(WE test-function "$FIX/bloated_skill" "$badedit")
bad_exit=$?
assert_contains "test-function: catches a genuinely deleted constraint" "$bad_out" "MISSING"
if [ "$bad_exit" -ne 0 ]; then
  echo "PASS: test-function: exits nonzero on a real regression"
else
  echo "FAIL: test-function: exits nonzero on a real regression"
  fail=1
fi

# --- suggest: assist level gates how many structured requests come back ----
none_out=$(WE suggest "$FIX/bloated_skill" --assist none --json)
slight_out=$(WE suggest "$FIX/bloated_skill" --assist slight --json)
lot_out=$(WE suggest "$FIX/bloated_skill" --assist lot --json)
none_n=$(python3 -c "import json,sys; print(len(json.loads(sys.argv[1])['requests']))" "$none_out")
slight_n=$(python3 -c "import json,sys; print(len(json.loads(sys.argv[1])['requests']))" "$slight_out")
lot_n=$(python3 -c "import json,sys; print(len(json.loads(sys.argv[1])['requests']))" "$lot_out")
[ "$none_n" -eq 0 ] && echo "PASS: suggest --assist none: no structured requests" || { echo "FAIL: suggest --assist none: expected 0 requests, got $none_n"; fail=1; }
[ "$slight_n" -eq 1 ] && echo "PASS: suggest --assist slight: exactly one request" || { echo "FAIL: suggest --assist slight: expected 1 request, got $slight_n"; fail=1; }
[ "$lot_n" -eq 3 ] && echo "PASS: suggest --assist lot: all three request kinds" || { echo "FAIL: suggest --assist lot: expected 3 requests, got $lot_n"; fail=1; }
assert_contains "suggest --assist slight: request is description_shorten" "$slight_out" '"kind": "description_shorten"'
assert_contains "suggest --assist lot: includes duplicate_consolidation" "$lot_out" '"kind": "duplicate_consolidation"'
assert_contains "suggest --assist lot: includes unnecessary_removal" "$lot_out" '"kind": "unnecessary_removal"'
none_human=$(WE suggest "$FIX/bloated_skill" --assist none)
assert_contains "suggest --assist none: points back to doing step 3 freehand" "$none_human" "freehand"

# --- apply-suggestion: mechanically applies an agent-authored answer, still
# gated by the SAME unchanged test-function check as a freehand edit --------
sugg_out="$SCRATCH/bloated_skill-suggested"
WE apply-suggestion "$FIX/bloated_skill" --answer "$FIX/answer_lot.json" --out "$sugg_out" >/tmp/we_apply_out.$$ 2>&1
apply_report=$(cat /tmp/we_apply_out.$$); rm -f /tmp/we_apply_out.$$
assert_contains "apply-suggestion: applies all three answer kinds" "$apply_report" "applied: description_shorten, duplicate_consolidation, unnecessary_removal"
assert_contains "apply-suggestion: new description landed in SKILL.md" "$(cat "$sugg_out/SKILL.md")" "confirmation-gated delete workflow"
dup_count=$(grep -c "never delete a data file without explicit user confirmation first" -i "$sugg_out/SKILL.md")
[ "$dup_count" -eq 1 ] && echo "PASS: apply-suggestion: consolidates 4 restatements down to 1 (state it once, delete the rest)" \
  || { echo "FAIL: apply-suggestion: expected the consolidated rule to appear once, found $dup_count"; fail=1; }

consolidated_func_out=$(WE test-function "$FIX/bloated_skill" "$sugg_out")
consolidated_func_exit=$?
assert_contains "apply-suggestion: unchanged test-function gate still runs against assist-applied output" "$consolidated_func_out" "MISSING"
if [ "$consolidated_func_exit" -ne 0 ]; then
  echo "PASS: apply-suggestion: an imperfect assist answer is still refused by the unchanged gate, not given a free pass"
else
  echo "FAIL: apply-suggestion: expected this fixture answer to trip test-function (it drops distinctive wording), gate did not catch it"
  fail=1
fi

# --- llm-assist config resolution: flag > env > .context-garden/config.yaml > default
cfg_root="$SCRATCH/cfg_repo"
mkdir -p "$cfg_root/.context-garden" "$cfg_root/nested"
cat > "$cfg_root/.context-garden/config.yaml" <<'EOF'
llm_assist:
  level: slight
EOF
cfg_level() { (cd "$cfg_root/nested" && python3 "$DIR/scripts/weeder.py" suggest "$FIX/bloated_skill" "$@" --json | python3 -c "import json,sys; print(json.load(sys.stdin)['assist_level'])"); }
assert_contains "config: .context-garden/config.yaml sets the default (walking up from nested cwd)" "$(cfg_level)" "slight"
assert_contains "config: env var overrides config.yaml" "$(CONTEXT_GARDEN_LLM_ASSIST=lot cfg_level)" "lot"
assert_contains "config: --assist flag overrides both env and config.yaml" "$(CONTEXT_GARDEN_LLM_ASSIST=lot cfg_level --assist none)" "none"
no_cfg_level=$(cd "$SCRATCH" && python3 "$DIR/scripts/weeder.py" suggest "$FIX/bloated_skill" --json | python3 -c "import json,sys; print(json.load(sys.stdin)['assist_level'])")
assert_contains "config: no config anywhere defaults to none" "$no_cfg_level" "none"

# --- UC3: vague description loses to a real competitor; tightened wins -----
vague_out=$(WE test-routing "$FIX/bloated_skill" --examples "$FIX/routing_examples.json" --competing "$FIX/competing_skill" --json)
tight_out=$(WE test-routing "$FIX/bloated_skill_tightened" --examples "$FIX/routing_examples.json" --competing "$FIX/competing_skill" --json)
vague_acc=$(python3 -c "import json,sys; print(json.load(sys.stdin)['accuracy'])" <<<"$vague_out")
tight_acc=$(python3 -c "import json,sys; print(json.load(sys.stdin)['accuracy'])" <<<"$tight_out")
assert_contains "routing: vague description loses a real positive-trigger prompt to a competitor" "$vague_out" "\"predicted\": \"file-organizer\""
if python3 -c "exit(0 if $tight_acc > $vague_acc else 1)"; then
  echo "PASS: routing: tightened description scores higher than vague ($tight_acc > $vague_acc)"
else
  echo "FAIL: routing: tightened description did not score higher ($tight_acc vs $vague_acc)"
  fail=1
fi

# --- negative prompts never trigger regardless of description quality -----
assert_not_contains "routing: negative prompts never predicted as this skill (vague)" "$vague_out" "\"kind\": \"negative\", \"expected\": \"no_trigger\", \"predicted\": \"bloated-skill\""

# --- diff: full before/after report shape matches the spec's example ------
diff_out=$(WE diff "$FIX/bloated_skill" "$opt_out" --examples "$FIX/routing_examples.json" --competing "$FIX/competing_skill")
assert_contains "diff: reports Before/After token sections" "$diff_out" "Before"
assert_contains "diff: reports always-loaded reduction percentage" "$diff_out" "Always-loaded reduction:"
assert_contains "diff: reports routing before/after" "$diff_out" "Routing (lexical-overlap proxy"
assert_contains "diff: reports functional constraint preservation" "$diff_out" "Constraints preserved: 7/7"

# --- clean error handling ---------------------------------------------------
bad_audit=$(WE audit "$FIX/does_not_exist" 2>&1)
assert_not_contains "audit: missing dir fails cleanly (no raw traceback header)" "$bad_audit" "Traceback (most recent call last)"

echo
if [ "$fail" -eq 0 ]; then
  echo "ALL CHECKS PASSED"
else
  echo "SOME CHECKS FAILED"
fi
exit "$fail"
