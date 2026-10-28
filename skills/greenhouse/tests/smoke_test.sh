#!/usr/bin/env bash
# Builds a scratch workspace with three repos (a shared library, an API that
# depends on it, a web client) and drives base creation, discovery, sync,
# links, brief, notes, staleness, curated-field preservation, the
# session-start hook and concurrent syncs. Offline.
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRATCH="$(mktemp -d)"
WS="$SCRATCH/platform"
trap 'rm -rf "$SCRATCH"' EXIT
unset GREENHOUSE_BASE
export CONTEXT_GARDEN_MODE_GREENHOUSE=on  # modes default to off; this tests the skill itself
G() { "$DIR/bin/greenhouse" "$@"; }

fail=0
assert_contains() {
  if grep -qF -- "$3" <<<"$2"; then echo "PASS: $1"; else echo "FAIL: $1 (expected: $3)"; echo "$2" | head -8; fail=1; fi
}
assert_not_contains() {
  if grep -qF -- "$3" <<<"$2"; then echo "FAIL: $1 (unexpected: $3)"; fail=1; else echo "PASS: $1"; fi
}
commit() { git add -A && git -c user.email=t@t -c user.name=t commit -qm "$1"; }
mkrepo() {  # $1 name; files are written by the caller afterwards
  mkdir -p "$WS/$1" && cd "$WS/$1" && git init -q . && git remote add origin "https://user:secret@git.example.com/acme/$1.git"
}

mkrepo shared-lib
cat > README.md <<'EOF'
# shared-lib

Money and currency types shared by every acme service; amounts are integer cents.
EOF
cat > pyproject.toml <<'EOF'
[project]
name = "acme-money"
dependencies = []
EOF
mkdir -p src/acme_money && echo "class Money: ..." > src/acme_money/__init__.py
cat > AGENTS.md <<'EOF'
<!-- seedbank:begin -->
- Money amounts are integer cents; never use float for currency.  [invariants]
- Release: bump version in pyproject.toml, tag vX.Y.Z.  [build]
<!-- seedbank:end -->
EOF
commit init

mkrepo orders-api
printf '# orders-api\n\nREST service for orders.\n' > README.md
cat > pyproject.toml <<'EOF'
[project]
name = "orders-api"
dependencies = ["acme_money>=2.0", "fastapi"]
[project.scripts]
orders-api = "orders.main:run"
EOF
mkdir -p orders && echo "x = 1" > orders/main.py
commit init

mkrepo web
cat > package.json <<'EOF'
{"name": "@acme/web", "dependencies": {"react": "^18"}, "scripts": {"build": "vite build"}}
EOF
echo "export {}" > index.ts
commit init

# --- no base yet
cd "$WS/orders-api"
out=$(G brief "anything" 2>&1)
assert_contains "brief without a base explains how to create one" "$out" "no greenhouse base found"
out=$(G hook session-start </dev/null)
assert_contains "hook without a base tells Claude to ask the user" "$out" "Ask the user (AskUserQuestion)"
out=$(G mode on </dev/null)
assert_contains "mode on without a base asks where it should be" "$out" "create a new base at"
out=$(G sync 2>&1)
assert_contains "sync never creates a base unasked" "$out" "no greenhouse base found"
[ ! -e "$WS/.greenhouse" ] && echo "PASS: no base created implicitly" || { echo "FAIL: base created"; fail=1; }

G sync --dry-run >/dev/null
[ ! -e "$WS/.greenhouse" ] && echo "PASS: dry-run writes nothing" || { echo "FAIL: dry-run created a base"; fail=1; }

# --- init creates the base beside the repos; siblings find it by ancestor search
cd "$WS/shared-lib"
out=$(G init --name acme)
assert_contains "init creates the base in the workspace" "$out" "$(cd "$WS" && pwd -P)/.greenhouse (created)"
assert_not_contains "no pointer needed when ancestor search finds it" "$out" "wrote"
out=$(G mode on </dev/null)
assert_contains "mode on reports the found base" "$out" "greenhouse base: "
out=$(G sync --summary "Money/currency types (integer cents) for every service" --provides "python package acme-money")
assert_contains "sync reports the card" "$out" "synced shared-lib"
cd "$WS/orders-api"
out=$(G where)
assert_contains "sibling repo discovers the base via ancestor" "$out" "found via ancestor"
out=$(G sync)
assert_contains "dependency on a provided package becomes a link" "$out" "uses: shared-lib (depends on acme_money)"
cd "$WS/web"
out=$(G sync --consumes "orders-api: GET /v1/orders")
assert_contains "missing summary is flagged" "$out" "no summary"

card=$(cat "$WS/.greenhouse/repos/orders-api.json")
assert_not_contains "credentials stripped from remote" "$card" "secret"
out=$(python3 -c "import json; print(json.load(open('$WS/.greenhouse/repos/orders-api.json'))['entrypoints'])")
assert_contains "entrypoint harvested" "$out" "['orders-api']"
card=$(cat "$WS/.greenhouse/repos/shared-lib.json")
assert_contains "seedbank hot facts harvested" "$card" "never use float for currency"

# --- brief from another repo
cd "$WS/orders-api"
out=$(G brief "store order total amount as currency")
assert_contains "brief lists this repo's links" "$out" "This repo uses: shared-lib"
assert_contains "brief pulls the matching fact from the linked repo" "$out" "never use float for currency"
assert_contains "brief lists downstream repos" "$out" "used by: web (consumes orders-api: GET /v1/orders)"
out=$(G brief "store order total amount as currency" --budget 40)
assert_not_contains "budget caps the brief" "$out" "never use float"

# --- platform notes
out=$(G note "API versioning" "All public HTTP APIs are versioned under /v<N>; breaking changes need a new version." --repo orders-api)
assert_contains "note recorded" "$out" "p001 API versioning [orders-api]"
out=$(G brief "add a breaking change to the orders http api")
assert_contains "brief returns the platform note" "$out" "[p001] API versioning"
# --- ranking: repos before notes, no empty headers, obsolete below live, exact slug wins
out=$(G brief "add a breaking change to the orders http api")
[ "$(grep -n 'Related repos:' <<<"$out" | cut -d: -f1)" -lt "$(grep -n 'Platform notes:' <<<"$out" | cut -d: -f1)" ] \
  && echo "PASS: brief lists repos before platform notes" || { echo "FAIL: notes before repos"; echo "$out"; fail=1; }
out=$(G brief "add a breaking change to the orders http api" --budget 5)
assert_not_contains "brief: no empty section headers" "$out" "Related repos:"
assert_contains "brief: says the matches did not fit" "$out" "raise --budget"
mkdir -p "$WS/orders-legacy" && (cd "$WS/orders-legacy" && git init -q . && echo x > a.txt && commit i \
  && G sync --summary "OBSOLETE orders http api, replaced by orders-api" >/dev/null)
out=$(G brief "orders http api")
[ "$(grep -n -- '- web:' <<<"$out" | cut -d: -f1)" -lt "$(grep -n -- '- orders-legacy' <<<"$out" | cut -d: -f1 || echo 99)" ] \
  && echo "PASS: brief ranks an obsolete repo below a live one" || { echo "FAIL: obsolete repo ranked first"; echo "$out"; fail=1; }
G forget orders-legacy >/dev/null && rm -rf "$WS/orders-legacy"
out=$(cd "$WS" && G brief "change the shared-lib money type")
assert_contains "brief: an exact slug in the task ranks that repo" "$(sed -n 3p <<<"$out")" "- shared-lib"
assert_not_contains "brief from the workspace: no 'this repo has no card'" "$out" "has no card yet"
cd "$WS/orders-api"
G note "API versioning" "updated text" >/dev/null
out=$(cat "$WS/.greenhouse/notes.json")
assert_contains "same topic replaces the note" "$out" "updated text"
assert_not_contains "old note text gone" "$out" "breaking changes need"
out=$(cat "$WS/.greenhouse/INDEX.md")
assert_contains "INDEX.md lists links" "$out" "orders-api -> shared-lib"

# --- curated fields survive a re-sync; staleness
cd "$WS/shared-lib" && G sync >/dev/null
out=$(G show shared-lib)
assert_contains "curated summary kept across sync" "$out" "integer cents) for every service"
echo "y" > src/acme_money/extra.py && commit more
cd "$WS/orders-api"
out=$(G list)
assert_contains "moved repo is flagged stale" "$out" "shared-lib [stale]"
out=$(G show shared-lib)
assert_contains "show warns on a stale card" "$out" "STALE"

# --- session-start hook
out=$(echo "{\"cwd\": \"$WS/orders-api\"}" | G hook session-start)
assert_contains "hook prints the platform overview" "$out" "[greenhouse] This repo is part of platform"
assert_contains "hook lists other repos" "$out" "- web:"
out=$(echo "{\"cwd\": \"$WS/orders-api\"}" | CONTEXT_GARDEN_MODE_GREENHOUSE=manual G hook session-start)
[ -z "$out" ] && echo "PASS: hook silent unless on" || { echo "FAIL: hook printed in manual mode"; fail=1; }

# --- opt-in auto-sync at session end
cd "$WS/web" && echo "z" > extra.ts && commit more
echo "{\"cwd\": \"$WS/web\"}" | G hook session-end
old=$(python3 -c "import json; print(json.load(open('$WS/.greenhouse/repos/web.json'))['head'])")
[ "$old" != "$(git rev-parse HEAD)" ] && echo "PASS: session-end does nothing by default" || { echo "FAIL: auto-synced without opt-in"; fail=1; }
out=$(G config auto_sync true)
assert_contains "config sets auto_sync" "$out" "auto_sync = True"
echo "{\"cwd\": \"$WS/web\"}" | G hook session-end
for _ in $(seq 1 50); do  # the sync runs in a detached child; give it up to 5 s
  new=$(python3 -c "import json; print(json.load(open('$WS/.greenhouse/repos/web.json'))['head'])")
  [ "$new" = "$(git rev-parse HEAD)" ] && break; sleep 0.1
done
[ "$new" = "$(git rev-parse HEAD)" ] && echo "PASS: session-end re-syncs when opted in" || { echo "FAIL: not re-synced"; fail=1; }
out=$(G show web)
assert_contains "auto-sync keeps curated fields" "$out" "consumes: orders-api: GET /v1/orders"

# --- workspace session (started in the folder of repos): overview + session-end of touched repos
out=$(echo "{\"cwd\": \"$WS\"}" | G hook session-start)
assert_contains "workspace overview: no 'this repo' claim" "$out" "This folder holds platform"
assert_not_contains "workspace overview: no card-missing line" "$out" "card is missing"
out=$(cd "$WS" && G sync 2>&1)
assert_contains "workspace sync: refuses outside a repo" "$out" "is not a git repo"
cd "$WS/web" && echo "w" > more.ts && commit more2
python3 -c 'import json,sys; print(json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use",
  "name": "Bash", "input": {"command": "cd web && npm test"}}]}}))' > "$SCRATCH/transcript.jsonl"
echo "{\"cwd\": \"$WS\", \"transcript_path\": \"$SCRATCH/transcript.jsonl\"}" | G hook session-end --wait
new=$(python3 -c "import json; print(json.load(open('$WS/.greenhouse/repos/web.json'))['head'])")
[ "$new" = "$(git rev-parse HEAD)" ] && echo "PASS: workspace session-end re-syncs the touched repo" || { echo "FAIL: touched repo not re-synced"; fail=1; }

# --- pointer file for a base outside the workspace
mkdir -p "$SCRATCH/elsewhere" && cd "$SCRATCH/elsewhere" && git init -q .
out=$(G init --at "$SCRATCH/central" --name central)
assert_contains "init elsewhere writes a pointer file" "$out" "wrote"
out=$(G where)
assert_contains "pointer file is followed" "$out" "pointer file"
cp .greenhouse .greenhouse.orig
out=$(G link "$WS/.greenhouse" 2>&1)
assert_contains "link never overwrites an existing pointer" "$out" "not overwriting"
cmp -s .greenhouse .greenhouse.orig && echo "PASS: pointer untouched" || { echo "FAIL: pointer changed"; fail=1; }

# --- a corrupt card is a bug to see, not to skip: commands and hooks crash (hook: exit 1, non-blocking)
cd "$WS/web" && echo "{" > "$WS/.greenhouse/repos/broken.json"
out=$(G list 2>&1)
assert_contains "corrupt card crashes list" "$out" "JSONDecodeError"
echo "{\"cwd\": \"$WS/web\"}" | G hook session-start >/dev/null 2>&1
rc=$?; [ "$rc" = "1" ] && echo "PASS: hook crash exits 1, never 2" || { echo "FAIL: hook exit $rc, want 1"; fail=1; }
rm "$WS/.greenhouse/repos/broken.json"

# --- concurrent syncs and notes (lock + atomic write)
for i in $(seq 1 8); do G note "topic $i" "fact $i" >/dev/null & done
for r in shared-lib orders-api web; do (cd "$WS/$r" && G sync >/dev/null) & done; wait
n=$(python3 -c "import json; print(len(json.load(open('$WS/.greenhouse/notes.json'))['notes']))")
[ "$n" = "9" ] && echo "PASS: 8 concurrent notes all kept (9 total)" || { echo "FAIL: $n notes, want 9"; fail=1; }
n=$(ls "$WS/.greenhouse/repos" | wc -l | tr -d ' ')
[ "$n" = "3" ] && echo "PASS: 3 cards after concurrent syncs" || { echo "FAIL: $n cards"; fail=1; }

if [ "$fail" -eq 0 ]; then echo "ALL CHECKS PASSED"; else echo "SOME CHECKS FAILED"; fi
exit "$fail"
