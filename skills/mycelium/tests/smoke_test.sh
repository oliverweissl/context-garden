#!/usr/bin/env bash
# Copies tests/fixtures/mini_repo to a scratch git repo and drives notes,
# brief, the pre/post-agent hooks (all gate rules and modes), staleness,
# install-agents and concurrent note writes. Offline.
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRATCH="$(mktemp -d)"
REPO="$SCRATCH/mini_repo"
cp -R "$DIR/tests/fixtures/mini_repo" "$REPO"
trap 'rm -rf "$SCRATCH"' EXIT
cd "$REPO"
git init -q . && git add -A && git -c user.email=t@t -c user.name=t commit -qm init
unset MYCELIUM_GATE
export CONTEXT_GARDEN_MODE_MYCELIUM=on  # modes default to off; this tests the skill itself
M() { "$DIR/bin/mycelium" "$@"; }

fail=0
assert_contains() {
  if grep -qF -- "$3" <<<"$2"; then echo "PASS: $1"; else echo "FAIL: $1 (expected: $3)"; echo "$2" | head -5; fail=1; fi
}
assert_not_contains() {
  if grep -qF -- "$3" <<<"$2"; then echo "FAIL: $1 (unexpected: $3)"; fail=1; else echo "PASS: $1"; fi
}
# hook payload: $1 session, $2 description, $3 prompt
hook() {
  python3 -c 'import json,sys; print(json.dumps({"session_id": sys.argv[1], "cwd": sys.argv[4], "tool_name": "Agent",
    "tool_input": {"description": sys.argv[2], "prompt": sys.argv[3], "subagent_type": "Explore"}}))' \
    "$1" "$2" "$3" "$REPO" | M hook pre-agent
}
FULL=$'Task: find where the payment webhook retries are configured\nWhy delegate: many unrelated modules\nReturn: file:line list\nBudget: 10 searches'

# --- notes + brief
out=$(M note "Auth token refresh" \
  "refresh_token in src/refresh.py renews within 60 s of expiry; called by auth_middleware" \
  src/refresh.py src/middleware.py)
assert_contains "note: recorded with hashed paths" "$out" "n001 Auth token refresh (2 path(s) hashed)"
out=$(M note "x" "y" src/nope.py 2>&1)
assert_contains "note: rejects a missing path" "$out" "no such path"
out=$(M brief "why does the auth token refresh fail")
assert_contains "brief: returns the matching note as Known block" "$out" "- [n001] Auth token refresh"
out=$(M brief "database migration ordering")
assert_contains "brief: unrelated task -> none relevant" "$out" "Known: none relevant"
[ -f .mycelium/.gitignore ] && echo "PASS: store created its .gitignore" || { echo "FAIL: no .gitignore"; fail=1; }

# --- gate rule 1: brief fields
out=$(hook s1 "look around" "Explore the repo and tell me about auth")
assert_contains "gate: missing fields -> deny" "$out" '"permissionDecision": "deny"'
assert_contains "gate: names the missing fields" "$out" '`Task:`, `Why delegate:`, `Return:`, `Budget:`'
assert_contains "gate: steers small work back to the main thread" "$out" "do it yourself"

# --- gate rule 3: known notes
out=$(hook s1 "auth refresh" $'Task: explain the auth token refresh path\nWhy delegate: x\nReturn: file:line\nBudget: 5 reads')
assert_contains "gate: matching notes without Known: -> deny with the note" "$out" "[n001] Auth token refresh"
out=$(hook s1 "auth refresh" $'Task: explain the auth token refresh path\nWhy delegate: x\nKnown: n001 refresh_token in src/refresh.py\nReturn: file:line\nBudget: 5 reads')
assert_not_contains "gate: full brief with Known: -> allow" "$out" "deny"

# --- gate rule 2: duplicates
out=$(hook s1 "auth refresh again" $'Task: explain the auth token refresh path again\nWhy delegate: x\nKnown: none relevant\nReturn: file:line\nBudget: 5 reads')
assert_contains "gate: near-duplicate in same session -> deny" "$out" 'repeats agent \"auth refresh\"'
out=$(hook s1 "auth refresh tests" $'Task: explain the auth token refresh path again\nNot a duplicate: now only the tests\nWhy delegate: x\nKnown: none relevant\nReturn: file:line\nBudget: 5 reads')
assert_not_contains "gate: Not a duplicate: line -> allow" "$out" "deny"
out=$(hook s2 "auth refresh" $'Task: explain the auth token refresh path\nWhy delegate: x\nKnown: n001\nReturn: file:line\nBudget: 5 reads')
assert_not_contains "gate: duplicates are per session" "$out" "deny"
out=$(hook s1 "webhooks" "$FULL")
assert_not_contains "gate: unrelated full brief -> allow" "$out" "deny"
lines=$(wc -l < .mycelium/spawns.jsonl | tr -d ' ')
[ "$lines" = "4" ] && echo "PASS: only allowed calls are logged (4)" || { echo "FAIL: spawns.jsonl has $lines lines, want 4"; fail=1; }

# --- modes + fail-open
out=$(MYCELIUM_GATE=warn hook s3 "x" "just look")
assert_contains "warn mode: allows, adds context" "$out" "additionalContext"
assert_not_contains "warn mode: no deny" "$out" '"permissionDecision"'
out=$(MYCELIUM_GATE=off hook s3 "x" "just look")
[ -z "$out" ] && echo "PASS: off mode: silent" || { echo "FAIL: off mode printed $out"; fail=1; }
out=$(echo 'not json' | M hook pre-agent; echo "rc=$?")
assert_contains "hook fails open on bad input" "$out" "rc=0"
out=$(echo '{"tool_name": "Read", "tool_input": {}}' | M hook pre-agent)
[ -z "$out" ] && echo "PASS: hook ignores non-Agent tools" || { echo "FAIL: non-Agent printed"; fail=1; }
out=$(echo "{\"tool_name\": \"Agent\", \"cwd\": \"$REPO\", \"tool_input\": {}}" | M hook post-agent)
assert_contains "post-agent: reminder to record findings" "$out" "bin/mycelium note"

# --- staleness
echo "# changed" >> src/refresh.py
out=$(M list --stale)
assert_contains "stale: edited file marks note stale" "$out" "STALE: src/refresh.py changed"
out=$(M brief "auth token refresh")
assert_contains "stale: brief still shows it, flagged" "$out" "agent must re-verify"
M verify n001 >/dev/null
out=$(M list --stale)
assert_contains "verify: re-hash clears staleness" "$out" "no notes"

# --- topic replace, status kind, drop
M note "auth token refresh" "updated wording" src/refresh.py >/dev/null
out=$(M list)
assert_contains "note: same topic replaces, keeps id" "$out" "[n001] auth token refresh: updated wording"
M note --kind status "sprint" "auth refresh half done" README.md >/dev/null
out=$(M brief "sprint auth refresh half done")
assert_not_contains "status notes are never briefed" "$out" "sprint"
M drop n002 >/dev/null; out=$(M list)
assert_not_contains "drop removes a note" "$out" "sprint"

# --- install-agents never overwrites
mkdir -p .claude/agents && echo "mine" > .claude/agents/scout.md
out=$(M install-agents)
assert_contains "install-agents keeps an existing file" "$out" "kept existing"
[ "$(cat .claude/agents/scout.md)" = "mine" ] && echo "PASS: user agent untouched" || { echo "FAIL: overwritten"; fail=1; }

# --- concurrent writers (lock + atomic write)
for i in $(seq 1 10); do M note "topic $i" "finding $i" README.md >/dev/null & done; wait
n=$(python3 -c 'import json; print(len(json.load(open(".mycelium/notes.json"))["notes"]))')
[ "$n" = "11" ] && echo "PASS: 10 concurrent notes all kept (11 total)" || { echo "FAIL: $n notes after concurrent writes, want 11"; fail=1; }

if [ "$fail" -eq 0 ]; then echo "ALL CHECKS PASSED"; else echo "SOME CHECKS FAILED"; fi
exit "$fail"
