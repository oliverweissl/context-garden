#!/usr/bin/env python3
"""mycelium: delegation gate + shared finding notes for subagents.

Commands: brief, note, list, verify, drop, install-agents,
hook pre-agent|post-agent|subagent-stop.
See references/protocol.md.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from my_gate import evaluate, format_notes  # noqa: E402
from my_store import Store  # noqa: E402

SKILL_DIR = Path(__file__).resolve().parent.parent
# Absolute wrapper path so printed handles run as-is (mycelium is not on PATH).
BIN = str(SKILL_DIR / "bin" / "mycelium")


def cmd_brief(store: Store, args) -> int:
    notes = store.search(args.query, limit=args.limit, min_overlap=1)
    if not notes:
        print("Known: none relevant")
        return 0
    print("Known:\n" + format_notes(store, notes))
    return 0


def cmd_note(store: Store, args) -> int:
    missing = [p for p in args.paths if not (store.repo_root / p).exists()]
    if missing:
        print(f"mycelium: no such path(s) under {store.repo_root}: {', '.join(missing)}", file=sys.stderr)
        return 2
    note = store.add_note(args.topic, args.text, args.paths, kind=args.kind)
    print(f"{note['id']} {note['topic']} ({len(note['paths'])} path(s) hashed)")
    return 0


def cmd_list(store: Store, args) -> int:
    notes = store.load_notes()
    if args.stale:
        notes = [n for n in notes if store.stale_paths(n)]
    if not notes:
        print("no notes")
        return 0
    print(format_notes(store, notes))
    return 0


def cmd_verify(store: Store, args) -> int:
    note = store.verify(args.note_id)
    if note is None:
        print(f"mycelium: no note {args.note_id}", file=sys.stderr)
        return 2
    print(f"{note['id']} re-hashed; now current")
    return 0


def cmd_drop(store: Store, args) -> int:
    if not store.drop(args.note_id):
        print(f"mycelium: no note {args.note_id}", file=sys.stderr)
        return 2
    print(f"dropped {args.note_id}")
    return 0


def cmd_install_agents(store: Store, args) -> int:
    dest = store.repo_root / ".claude" / "agents"
    dest.mkdir(parents=True, exist_ok=True)
    for src in sorted((SKILL_DIR / "agents").glob("*.md")):
        target = dest / src.name
        if target.exists():
            print(f"kept existing {target}")
            continue
        shutil.copy2(src, target)
        print(f"installed {target}")
    return 0


def _hook_output(event: str, *, deny: str = "", context: str = "") -> str:
    out = {"hookSpecificOutput": {"hookEventName": event}}
    if deny:
        out["hookSpecificOutput"]["permissionDecision"] = "deny"
        out["hookSpecificOutput"]["permissionDecisionReason"] = deny
    if context:
        out["hookSpecificOutput"]["additionalContext"] = context
    return json.dumps(out)


_NOTES_HEADER = re.compile(r"^\s*(?:\*\*)?notes:(?:\*\*)?\s*$", re.I)
_NOTE_LINE = re.compile(r"^\s*[-*]\s+(.+?)\s+\|\s+(.+?)\s+\|\s+(.+?)\s*$")


def parse_notes(text: str) -> list:
    """(topic, finding, [paths]) from a `Notes:` block of an agent's reply:
    lines `- <topic> | <finding> | <file> [<file>...]` up to the first blank
    or non-bullet line. `path:line` is reduced to the path."""
    out, inside = [], False
    for line in (text or "").splitlines():
        if _NOTES_HEADER.match(line):
            inside = True
            continue
        if not inside:
            continue
        m = _NOTE_LINE.match(line)
        if not m:
            if out or line.strip():
                break
            continue
        paths = [re.sub(r":\d+(?:-\d+)?$", "", p.strip("`,")) for p in m.group(3).split()]
        out.append((m.group(1).strip("`*"), m.group(2), paths))
    return out


def record_agent_notes(store: Store, text: str) -> list:
    """Record each well-formed note whose files exist; the rest are skipped
    (a finding without a file to hash could never be marked stale)."""
    recorded = []
    for topic, finding, paths in parse_notes(text):
        real = [p for p in paths if (store.repo_root / p).is_file()]
        if real:
            recorded.append(store.add_note(topic, finding, real)["id"])
    return recorded


def cmd_hook(store: Store, args) -> int:
    """Claude Code hook entry. Fails open: any error allows the tool call."""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if args.event == "subagent-stop":
            # Runs in the parent's harness, under the store lock: still "only
            # the parent writes notes", without the parent spending a turn on it.
            if payload.get("cwd"):
                os.chdir(payload["cwd"])
                store = Store()
            record_agent_notes(store, payload.get("last_assistant_message") or "")
            return 0
        if payload.get("tool_name") not in ("Agent", "Task"):
            return 0
        if payload.get("cwd"):  # resolve the store from the session's dir, not the hook's
            os.chdir(payload["cwd"])
            store = Store()
        mode = store.config()["gate"]
        if mode == "off":
            return 0
        session = payload.get("session_id") or "unknown"
        tool_input = payload.get("tool_input") or {}
        if args.event == "post-agent":
            print(_hook_output("PostToolUse", context=(
                "mycelium: the agent's `Notes:` lines were recorded automatically. Record any "
                "other durable fact it found (where something lives, how it works) once: "
                f"`{BIN} note \"<topic>\" \"<finding>\" <file>...`."
            )))
            return 0
        with store.lock():
            verdict, message = evaluate(store, session, tool_input, BIN)
            if verdict == "allow":
                store.log_spawn(session, tool_input.get("description", ""), tool_input.get("prompt", ""))
        if verdict == "deny":
            if mode == "warn":
                print(_hook_output("PreToolUse", context=message))
            else:
                print(_hook_output("PreToolUse", deny=message))
    except Exception:  # never break the Agent tool
        return 0
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="mycelium", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("brief", help="print notes relevant to a task, as a `Known:` block")
    b.add_argument("query")
    b.add_argument("--limit", type=int, default=5)

    n = sub.add_parser("note", help="record a finding (replaces the note with the same topic)")
    n.add_argument("topic")
    n.add_argument("text")
    n.add_argument("paths", nargs="+", help="repo-relative files the finding describes")
    n.add_argument("--kind", choices=["finding", "status"], default="finding")

    ls = sub.add_parser("list", help="list notes")
    ls.add_argument("--stale", action="store_true", help="only notes whose files changed")

    v = sub.add_parser("verify", help="mark a note current after re-checking it")
    v.add_argument("note_id")
    d = sub.add_parser("drop", help="delete a note")
    d.add_argument("note_id")

    sub.add_parser("install-agents", help="copy agent templates to <repo>/.claude/agents/ (never overwrites)")

    h = sub.add_parser("hook", help="Claude Code hook entry (reads the hook JSON on stdin)")
    h.add_argument("event", choices=["pre-agent", "post-agent", "subagent-stop"])

    args = p.parse_args(argv)
    handlers = {
        "brief": cmd_brief, "note": cmd_note, "list": cmd_list, "verify": cmd_verify,
        "drop": cmd_drop, "install-agents": cmd_install_agents, "hook": cmd_hook,
    }
    return handlers[args.cmd](Store(), args)


if __name__ == "__main__":
    sys.exit(main())
