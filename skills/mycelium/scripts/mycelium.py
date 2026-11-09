#!/usr/bin/env python3
"""mycelium: delegation gate + shared finding notes for subagents.

Commands: brief, note, list, verify, drop, install-agents, budget,
hook pre-agent|budget|subagent-stop (post-agent is a no-op kept for old settings).
See references/protocol.md.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import my_budget  # noqa: E402
from my_gate import LEVELS, default_budget, evaluate, format_notes, level  # noqa: E402
from my_store import Store, split_by_repo, workspace_of, workspace_stores  # noqa: E402

SKILL_DIR = Path(__file__).resolve().parent.parent
# Absolute wrapper path so printed handles run as-is (mycelium is not on PATH).
# Shell-quoted: printed for the agent to run, and plugin paths may contain spaces.
BIN = shlex.quote(str(SKILL_DIR / "bin" / "mycelium"))


def cmd_brief(store: Store, args) -> int:
    notes = store.search(args.query, limit=args.limit, min_overlap=1)
    if not notes:
        print("Known: none relevant")
        return 0
    print("Known:\n" + format_notes(store, notes))
    return 0


def cmd_note(store: Store, args) -> int:
    ws = workspace_of(Path.cwd())
    if ws is not None:  # workspace: one note per repo holding the paths
        missing = [p for p in args.paths if not (ws / p).exists()]
        by_repo = split_by_repo(args.paths, ws) if not missing else {}
        if missing or not by_repo:
            print(f"mycelium: no such path(s) in a repo under {ws}: "
                  f"{', '.join(missing or args.paths)}", file=sys.stderr)
            return 2
        for root, rel in by_repo.items():
            note = Store(root).add_note(args.topic, args.text, rel, kind=args.kind)
            print(f"{root.name}: {note['id']} {note['topic']} ({len(note['paths'])} path(s) hashed)")
        return 0
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


def cmd_budget(store: Store, args) -> int:
    """Show or set the budget level that scales agents' default tool budgets."""
    cfg = store.config()
    if args.level:
        store.set_config("budget_level", args.level)
        cfg["budget_level"] = args.level
    lvl = level(cfg)
    print(f"budget level: {lvl}"
          + ("" if args.level or lvl == cfg.get("budget_level", "medium") else " (from the plugin option or MYCELIUM_BUDGET)"))
    for t in ("scout", "botanist", "landscaper", "Explore", "general-purpose", "default"):
        print(f"  {t}: {default_budget(t, cfg)} tool calls")
    return 0


def _hook_output(event: str, *, deny: str = "", context: str = "", update: dict | None = None) -> str:
    out = {"hookSpecificOutput": {"hookEventName": event}}
    if deny:
        out["hookSpecificOutput"]["permissionDecision"] = "deny"
        out["hookSpecificOutput"]["permissionDecisionReason"] = deny
    elif update is not None:
        out["hookSpecificOutput"]["permissionDecision"] = "allow"
        out["hookSpecificOutput"]["permissionDecisionReason"] = "mycelium: brief completed"
        out["hookSpecificOutput"]["updatedInput"] = update
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
    (a finding without a file to hash could never be marked stale). In a
    workspace each note is filed in the repo(s) holding its files."""
    recorded = []
    ws = workspace_of(store.repo_root)
    for topic, finding, paths in parse_notes(text):
        if ws is not None:
            for root, rel in split_by_repo(paths, ws).items():
                real = [p for p in rel if (root / p).is_file()]
                if real:
                    recorded.append(Store(root).add_note(topic, finding, real)["id"])
            continue
        real = [p for p in paths if (store.repo_root / p).is_file()]
        if real:
            recorded.append(store.add_note(topic, finding, real)["id"])
    return recorded


def cmd_hook(store: Store, args) -> int:
    """Claude Code hook entry. Prints nothing on no-op paths; an error raises
    (exit 1, a non-blocking hook error the user sees) and never denies the call."""
    payload = json.loads(sys.stdin.read() or "{}")
    if args.event == "post-agent":
        return 0  # the reminder it printed is gone: SubagentStop records Notes: itself
    if payload.get("cwd"):  # resolve the store from the session's dir, not the hook's
        os.chdir(payload["cwd"])
        store = Store()
    if args.event == "subagent-stop":
        # Runs in the parent's harness, under the store lock: still "only
        # the parent writes notes", without the parent spending a turn on it.
        my_budget.forget(payload.get("session_id", ""), payload.get("agent_id", ""))
        record_agent_notes(store, payload.get("last_assistant_message") or "")
        return 0
    if args.event == "budget":
        answer = my_budget.decide(payload, store.config())
        if answer:
            print(json.dumps(answer))
        return 0
    if payload.get("tool_name") not in ("Agent", "Task"):
        return 0
    session = payload.get("session_id") or "unknown"
    tool_input = payload.get("tool_input") or {}
    ws = workspace_of(store.repo_root)
    if ws is not None:
        store = workspace_stores(ws, f"{tool_input.get('description', '')}\n{tool_input.get('prompt', '')}")
    mode = store.config()["gate"]
    if mode == "off":
        return 0
    with store.lock():
        result = evaluate(store, session, tool_input, BIN)
        if "deny" not in result:
            ran = result["input"] or tool_input
            store.log_spawn(session, ran.get("description", ""), ran.get("prompt", ""))
    if "deny" in result:
        key = "context" if mode == "warn" else "deny"
        print(_hook_output("PreToolUse", **{key: result["deny"]}))
    elif mode == "warn":
        # warn: say what enforce would change, change nothing.
        what = "would complete the brief" if result["input"] else ""
        text = "; ".join(t for t in (what, result["context"]) if t)
        if text:
            print(_hook_output("PreToolUse", context=f"mycelium: {text}"))
    elif result["input"] or result["context"]:
        print(_hook_output("PreToolUse", context=result["context"], update=result["input"]))
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

    bl = sub.add_parser("budget", help="show or set the level scaling agents' default tool budgets")
    bl.add_argument("level", nargs="?", choices=sorted(LEVELS, key=LEVELS.get))

    h = sub.add_parser("hook", help="Claude Code hook entry (reads the hook JSON on stdin)")
    h.add_argument("event", choices=["pre-agent", "budget", "post-agent", "subagent-stop"])

    args = p.parse_args(argv)
    handlers = {
        "brief": cmd_brief, "note": cmd_note, "list": cmd_list, "verify": cmd_verify,
        "drop": cmd_drop, "install-agents": cmd_install_agents, "budget": cmd_budget,
        "hook": cmd_hook,
    }
    store = Store()
    ws = workspace_of(store.repo_root)
    if ws is not None:  # a folder of repos: read the repos' stores, not its own
        if args.cmd in ("verify", "drop"):
            print("mycelium: note ids are per repo; run this from inside the repo "
                  f"(cd <repo> && {BIN} {args.cmd} {args.note_id})", file=sys.stderr)
            return 2
        if args.cmd in ("brief", "list"):
            store = workspace_stores(ws, getattr(args, "query", ""))
    return handlers[args.cmd](store, args)


if __name__ == "__main__":
    sys.exit(main())
