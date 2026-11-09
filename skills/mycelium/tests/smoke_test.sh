#!/usr/bin/env bash
# Copies tests/fixtures/mini_repo to a scratch git repo and drives notes,
# brief, the pre-agent gate (all rules and modes), the per-agent budget hook,
# budget levels, staleness,
# install-agents and concurrent note writes. Offline.
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRATCH="$(mktemp -d)"
REPO="$SCRATCH/mini_repo"
cp -R "$DIR/tests/fixtures/mini_repo" "$REPO"
trap 'rm -rf "$SCRATCH" "${TMPDIR:-/tmp}/mycelium-budget/bs-$$"' EXIT
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

# --- gate rule 1: brief completed, not denied
out=$(hook s1 "look around" "Explore the repo and tell me about auth")
assert_not_contains "gate: incomplete brief is never denied" "$out" '"deny"'
assert_contains "gate: incomplete brief is rewritten" "$out" '"updatedInput"'
assert_contains "gate: Task taken from the description" "$out" 'Task: look around'
assert_contains "gate: default Return added" "$out" 'Return: answer first'
assert_contains "gate: default Budget for the type" "$out" 'Budget: 25 tool calls'

# --- gate rule 2: known notes appended, not denied
out=$(hook s2 "auth refresh" $'Task: explain the auth token refresh path\nReturn: file:line\nBudget: 5 reads')
assert_contains "gate: matching notes added as Known" "$out" "[n001] Auth token refresh"
assert_not_contains "gate: matching notes do not deny" "$out" '"deny"'
out=$(hook s3 "auth refresh" $'Task: explain the auth token refresh path\nKnown: n001 refresh_token in src/refresh.py\nReturn: file:line\nBudget: 5 reads')
[ -z "$out" ] && echo "PASS: gate: complete brief passes silently" || { echo "FAIL: complete brief printed $out"; fail=1; }

# --- gate rule 4: duplicates
out=$(hook s3 "auth refresh again" $'Task: explain the auth token refresh path\nKnown: none relevant\nReturn: file:line\nBudget: 5 reads')
assert_contains "gate: identical repeat in same session -> deny" "$out" 'repeats agent \"auth refresh\"'
out=$(hook s3 "auth refresh slow" $'Task: explain why the auth token refresh path is slow\nKnown: none relevant\nReturn: file:line\nBudget: 5 reads')
assert_not_contains "gate: loose overlap is not denied" "$out" '"deny"'
assert_contains "gate: loose overlap gets a hint" "$out" 'resembles agent'
out=$(hook s3 "again" $'Task: explain the auth token refresh path\nNot a duplicate: other branch\nKnown: none relevant\nReturn: file:line\nBudget: 5 reads')
assert_not_contains "gate: Not a duplicate: line -> allow" "$out" '"deny"'
out=$(hook s4 "auth refresh" $'Task: explain the auth token refresh path\nKnown: none relevant\nReturn: file:line\nBudget: 5 reads')
assert_not_contains "gate: duplicates are per session" "$out" '"deny"'

# --- gate rule 3: general-purpose routed to a specialist
gp() {
  python3 -c 'import json,sys; print(json.dumps({"session_id": sys.argv[1], "cwd": sys.argv[3], "tool_name": "Agent",
    "tool_input": {"description": "d", "prompt": sys.argv[2], "subagent_type": "general-purpose"}}))' \
    "$1" "$2" "$REPO" | CLAUDE_PLUGIN_ROOT=/x M hook pre-agent
}
B=$'\nKnown: none relevant\nReturn: x\nBudget: 9'
assert_contains "route: read-only task -> scout" "$(gp r1 "Task: where is the webhook retry configured$B")" '"subagent_type": "context-garden:scout"'
assert_contains "route: edit task -> landscaper" "$(gp r2 "Task: fix the webhook retry off-by-one$B")" '"subagent_type": "context-garden:landscaper"'
assert_contains "route: run task -> botanist" "$(gp r3 "Task: run the tests for payments$B")" '"subagent_type": "context-garden:botanist"'
assert_not_contains "route: Why general: keeps it" "$(gp r4 $'Task: where is x\nWhy general: needs web'"$B")" 'subagent_type'
out=$(python3 -c 'import json,sys; print(json.dumps({"session_id": "r5", "cwd": sys.argv[1], "tool_name": "Agent",
  "tool_input": {"description": "d", "prompt": "Task: where is x\nKnown: none relevant\nReturn: x\nBudget: 9", "subagent_type": "general-purpose"}}))' "$REPO" \
  | env -u CLAUDE_PLUGIN_ROOT HOME="$SCRATCH" "$DIR/bin/mycelium" hook pre-agent)
[ -z "$out" ] && echo "PASS: route: no specialist installed -> untouched" || { echo "FAIL: routed without agents: $out"; fail=1; }

# --- budget levels
out=$(M budget)
assert_contains "budget: default level medium" "$out" "budget level: medium"
assert_contains "budget: landscaper default" "$out" "landscaper: 40 tool calls"
M budget low >/dev/null
assert_contains "budget: low halves defaults" "$(M budget)" "landscaper: 20 tool calls"
assert_contains "budget: plugin option wins" "$(CLAUDE_PLUGIN_OPTION_AGENT_BUDGET=extreme M budget)" "landscaper: 160 tool calls"
out=$(hook s5 "x" "Task: list the auth modules")
assert_contains "budget: level scales the injected Budget line" "$out" 'Budget: 12 tool calls'
M budget medium >/dev/null

# --- hard budget per subagent
TR="$SCRATCH/sess.jsonl"; mkdir -p "$SCRATCH/sess/subagents"
python3 -c 'import json,sys; print(json.dumps({"type": "user", "message": {"role": "user", "content": "Task: t\nBudget: 6 tool calls"}}))' \
  > "$SCRATCH/sess/subagents/agent-a1.jsonl"
call() {
  python3 -c 'import json,sys; print(json.dumps({"session_id": "bs-" + sys.argv[4], "cwd": sys.argv[1], "transcript_path": sys.argv[2],
    "tool_name": "Read", "tool_input": {}, **({"agent_id": sys.argv[3], "agent_type": "general-purpose"} if sys.argv[3] else {})}))' \
    "$REPO" "$TR" "$1" "$$" | M hook budget
}
out=$(call ""); [ -z "$out" ] && echo "PASS: budget: main-thread calls pass silently" || { echo "FAIL: main thread printed $out"; fail=1; }
out=$(call a1; call a1); [ -z "$out" ] && echo "PASS: budget: early calls silent" || { echo "FAIL: early calls printed $out"; fail=1; }
assert_contains "budget: wrap-up line 3 calls before the brief's limit" "$(call a1)" "3 tool calls left of 6"
out=$(call a1; call a1; call a1)
assert_not_contains "budget: calls up to the limit allowed" "$out" '"deny"'
assert_contains "budget: past the limit -> deny with reply-now" "$(call a1)" "tool budget used up (6 calls)"
python3 -c 'import json,sys; print(json.dumps({"session_id": "bs-" + sys.argv[2], "cwd": sys.argv[1], "agent_id": "a1", "last_assistant_message": ""}))' "$REPO" "$$" \
  | M hook subagent-stop
out=$(call a1); [ -z "$out" ] && echo "PASS: budget: counters dropped at SubagentStop" || { echo "FAIL: counter kept: $out"; fail=1; }
for i in $(seq 1 47); do call a2 >/dev/null; done
assert_contains "budget: no Budget line -> type default (40) applies" "$(call a2)" "used up (40 calls)"

lines=$(wc -l < .mycelium/spawns.jsonl | tr -d ' ')
[ "$lines" = "12" ] && echo "PASS: only allowed calls are logged (12)" || { echo "FAIL: spawns.jsonl has $lines lines, want 12"; fail=1; }

# --- modes + hook errors
out=$(MYCELIUM_GATE=warn hook s3 "x" "just look")
assert_contains "warn mode: allows, adds context" "$out" "additionalContext"
assert_not_contains "warn mode: no deny" "$out" '"permissionDecision"'
out=$(MYCELIUM_GATE=off hook s3 "x" "just look")
[ -z "$out" ] && echo "PASS: off mode: silent" || { echo "FAIL: off mode printed $out"; fail=1; }
out=$(echo 'not json' | M hook pre-agent 2>/dev/null); rc=$?
[ "$rc" = "1" ] && [ -z "$out" ] && echo "PASS: hook error exits 1 (non-blocking), stdout empty" \
  || { echo "FAIL: hook error rc=$rc out=$out (want 1, empty)"; fail=1; }
out=$(echo '{"tool_name": "Read", "tool_input": {}}' | M hook pre-agent)
[ -z "$out" ] && echo "PASS: hook ignores non-Agent tools" || { echo "FAIL: non-Agent printed"; fail=1; }
out=$(echo "{\"tool_name\": \"Agent\", \"cwd\": \"$REPO\", \"tool_input\": {}}" | M hook post-agent)
[ -z "$out" ] && echo "PASS: post-agent: no reminder (kept as a no-op)" || { echo "FAIL: post-agent printed $out"; fail=1; }

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

# --- subagent-stop: Notes: block of the agent's reply becomes notes
stop() {
  python3 -c 'import json,sys; print(json.dumps({"session_id": "s9", "cwd": sys.argv[2],
    "hook_event_name": "SubagentStop", "last_assistant_message": sys.argv[1]}))' "$1" "$REPO" | M hook subagent-stop
}
stop $'Answer: middleware checks tokens\nNotes:\n- Request middleware | auth_middleware wraps every handler | src/middleware.py:3\n- Ghost | lives nowhere | src/ghost.py\nUnresolved: none'
out=$(M list)
assert_contains "subagent-stop: Notes: line recorded" "$out" "Request middleware: auth_middleware wraps every handler"
assert_not_contains "subagent-stop: note without an existing file skipped" "$out" "Ghost"
CONTEXT_GARDEN_MODE_MYCELIUM=off stop $'Notes:\n- Off topic | should not land | README.md'
assert_not_contains "subagent-stop: silent while off" "$(M list)" "Off topic"

# --- install-agents never overwrites
mkdir -p .claude/agents && echo "mine" > .claude/agents/scout.md
M install-agents >/dev/null
[ "$(cat .claude/agents/scout.md)" = "mine" ] && echo "PASS: user agent untouched" || { echo "FAIL: overwritten"; fail=1; }

# --- a corrupt notes.json raises instead of being overwritten
cp .mycelium/notes.json "$SCRATCH/notes.bak"; echo '{broken' > .mycelium/notes.json
M note "z" "z" README.md >/dev/null 2>&1; rc=$?
[ "$rc" != "0" ] && [ "$(cat .mycelium/notes.json)" = "{broken" ] && echo "PASS: corrupt notes.json: note fails, file kept" \
  || { echo "FAIL: corrupt notes.json rc=$rc, file now: $(head -c 80 .mycelium/notes.json)"; fail=1; }
cp "$SCRATCH/notes.bak" .mycelium/notes.json

# --- concurrent writers (lock + atomic write)
for i in $(seq 1 10); do M note "topic $i" "finding $i" README.md >/dev/null & done; wait
n=$(python3 -c 'import json; print(len(json.load(open(".mycelium/notes.json"))["notes"]))')
[ "$n" = "12" ] && echo "PASS: 10 concurrent notes all kept (12 total)" || { echo "FAIL: $n notes after concurrent writes, want 12"; fail=1; }

# --- workspace: a session started in a folder of repos files notes per repo
WSP="$SCRATCH/ws"; mkdir -p "$WSP/alpha/src" "$WSP/beta"
(cd "$WSP/alpha" && git init -q . && echo 'def refresh(): pass' > src/auth.py)
(cd "$WSP/beta" && git init -q . && echo 'all:' > Makefile)
cd "$WSP"
out=$(M note "Auth refresh" "refresh in auth.py, built by beta" alpha/src/auth.py beta/Makefile)
assert_contains "workspace note: filed in alpha" "$out" "alpha: n001"
assert_contains "workspace note: filed in beta" "$out" "beta: n001"
[ ! -e "$WSP/.mycelium" ] && echo "PASS: workspace note: no store in the workspace" || { echo "FAIL: store created in workspace"; fail=1; }
out=$(M brief "where is auth refresh")
assert_contains "workspace brief: workspace-relative paths, repo-prefixed id" "$out" "[alpha/n001] Auth refresh"
out=$(python3 -c 'import json,sys; print(json.dumps({"session_id": "w1", "cwd": sys.argv[1], "tool_name": "Agent",
  "tool_input": {"description": "auth", "prompt": "Task: explain auth refresh in alpha/src\nWhy delegate: x\nReturn: y\nBudget: 3"}}))' "$WSP" \
  | M hook pre-agent)
assert_contains "workspace gate: notes of the named repo are required" "$out" "alpha/src/auth.py"
python3 -c 'import json,sys; print(json.dumps({"session_id": "w1", "cwd": sys.argv[1],
  "last_assistant_message": "Notes:\n- Beta build | beta builds with make | beta/Makefile"}))' "$WSP" | M hook subagent-stop
assert_contains "workspace subagent-stop: note lands in beta" "$(cd beta && M list)" "Beta build"
cd "$REPO"

if [ "$fail" -eq 0 ]; then echo "ALL CHECKS PASSED"; else echo "SOME CHECKS FAILED"; fi
exit "$fail"
