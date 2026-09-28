#!/usr/bin/env bash
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
assert_contains "diff: reports SKILL.md reduction percentage" "$diff_out" "SKILL.md (description + on-trigger body) reduction:"
assert_contains "diff: reports routing before/after" "$diff_out" "Routing (lexical-overlap proxy"
assert_contains "diff: reports functional constraint preservation" "$diff_out" "Constraints preserved: 7/7"

# --- regressions: --out overlapping the source, fences, polarity, YAML ----
over_out=$(WE optimize "$opt_out" --out "$opt_out" 2>&1); over_exit=$?
if [ "$over_exit" -ne 0 ] && [ -f "$opt_out/SKILL.md" ]; then
  echo "PASS: optimize: refuses --out equal to the source (source kept)"
else
  echo "FAIL: optimize: --out equal to the source must be refused ($over_out)"; fail=1
fi
WE apply-suggestion "$opt_out" --answer "$FIX/answer_lot.json" --out "$opt_out/nested" >/dev/null 2>&1 \
  && { echo "FAIL: apply-suggestion: --out inside the source must be refused"; fail=1; } \
  || echo "PASS: apply-suggestion: refuses --out inside the source"

fence="$SCRATCH/fence_skill"; mkdir -p "$fence"
printf -- '---\nname: fence\ndescription: >\n  multi-line\n  folded description\n---\n\n# Fence\n\n## Workflow\n\n```bash\n# Examples of a comment, not a heading\necho hi\n```\n\nNever skip the\nconfirmation step.\n' > "$fence/SKILL.md"
fence_audit=$(WE audit "$fence" --json)
assert_not_contains "audit: '#' inside a fenced code block is not a heading" "$fence_audit" '"heading": "Examples of a comment'
flipped="$SCRATCH/fence_flipped"; cp -R "$fence" "$flipped"
sed -i.bak 's/^Never skip the$/Always skip the/' "$flipped/SKILL.md"
flip_out=$(WE test-function "$fence" "$flipped"); flip_exit=$?
assert_contains "test-function: joins a hard-wrapped constraint into one sentence" "$flip_out" "Never skip the confirmation step."
[ "$flip_exit" -ne 0 ] && echo "PASS: test-function: Never -> Always polarity flip is caught" \
  || { echo "FAIL: test-function: Never -> Always polarity flip passed"; fail=1; }
printf '{"description_shorten": {"new_description": "Use: when \\"x\\" \\\\1 happens # really"}}' > "$SCRATCH/desc.json"
WE apply-suggestion "$fence" --answer "$SCRATCH/desc.json" --out "$SCRATCH/fence_desc" >/dev/null
desc_json=$(WE audit "$SCRATCH/fence_desc" --json)
assert_contains "apply-suggestion: replaces a whole folded description, safely quoted" "$desc_json" '"description": "Use: when \"x\" \\1 happens # really"'
assert_not_contains "apply-suggestion: no leftover continuation lines" "$(cat "$SCRATCH/fence_desc/SKILL.md")" "folded description"

# --- filler detector: never flags instructions, >= 80% recall on filler ----
filler_out=$(python3 - "$DIR/scripts" "$FIX/filler_sentences.json" <<'PY'
import json, sys
sys.path.insert(0, sys.argv[1])
from we_filler import is_filler
d = json.load(open(sys.argv[2]))
bad = [s for s in d["instruction"] if is_filler(s)]
hit = sum(is_filler(s) for s in d["filler"])
print(f"instruction_flagged={len(bad)} filler_recall={hit / len(d['filler']):.2f}")
for s in bad:
    print(f"  wrongly flagged: {s}")
sys.exit(0 if not bad and hit / len(d["filler"]) >= 0.8 else 1)
PY
); filler_exit=$?
[ "$filler_exit" -eq 0 ] && echo "PASS: filler: 0 instruction sentences flagged, recall >= 80% ($filler_out)" \
  || { echo "FAIL: filler: $filler_out"; fail=1; }
assert_not_contains "audit: 'you should always be careful' (a constraint) is not filler" "$audit_out" "As an AI assistant, it's important to note"

# --- token estimates are labelled; calibration file changes the ratio -------
assert_contains "audit: uncalibrated estimate is labelled" \
  "$(WEEDER_TOKEN_CALIBRATION="$SCRATCH/none.json" WE audit "$FIX/bloated_skill")" "estimated (uncalibrated chars/4)"
cat > "$SCRATCH/cal.json" <<'EOF'
{"date": "2026-01-02", "categories": {"markdown": {"chars_per_token": 2.0}, "code": {"chars_per_token": 2.0}, "yaml": {"chars_per_token": 2.0}}, "overall_error_range_pct": [-3.5, 4.25]}
EOF
cal_audit=$(WEEDER_TOKEN_CALIBRATION="$SCRATCH/cal.json" WE audit "$FIX/bloated_skill")
assert_contains "audit: calibrated estimate is labelled with its date" "$cal_audit" "estimated (calibrated 2026-01-02)"
assert_contains "audit: calibrated estimate reports observed error range" "$cal_audit" "-3.5% to +4.2%"
uncal_desc=$(WEEDER_TOKEN_CALIBRATION="$SCRATCH/none.json" WE audit "$FIX/bloated_skill" --json | python3 -c "import json,sys; print(json.load(sys.stdin)['description_tokens'])")
cal_desc=$(WEEDER_TOKEN_CALIBRATION="$SCRATCH/cal.json" WE audit "$FIX/bloated_skill" --json | python3 -c "import json,sys; print(json.load(sys.stdin)['description_tokens'])")
python3 -c "exit(0 if abs($cal_desc - 2 * $uncal_desc) <= 1 else 1)" \
  && echo "PASS: tokens: calibrated 2.0 chars/token doubles the chars/4 estimate ($uncal_desc -> $cal_desc)" \
  || { echo "FAIL: tokens: expected ~2x ($uncal_desc -> $cal_desc)"; fail=1; }

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
