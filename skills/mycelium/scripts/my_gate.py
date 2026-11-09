"""Delegation gate: make an Agent call cheap to run as written.

A denied call costs the parent a full resend of its brief, so the gate fixes
briefs instead of refusing them (PreToolUse `updatedInput`):
  1. brief   -- a missing `Task:` is taken from the description; missing
                `Return:` and `Budget:` lines get defaults (`Why delegate:` is
                no longer required)
  2. known   -- fresh notes that match the task are added as a `Known:` block
                when the brief has none
  3. route   -- `general-purpose` (or no type) runs as a specialist when one
                fits: a read-only task as `scout`, a test/build run as
                `botanist`, an edit as `landscaper`; a `Why general:` line keeps it
  4. repeat  -- only a near-identical repeat of an agent already spawned this
                session is denied (`Not a duplicate:` skips it); a looser
                overlap passes with a one-line hint
The budget a brief states is enforced per agent by my_budget (hard limit).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from my_store import Store, jaccard, words

_FIELD = r"^\s*[-*]?\s*\**{}\**\s*:"
_TASK_RE = re.compile(_FIELD.format("Task"), re.I | re.M)
_RETURN_RE = re.compile(_FIELD.format("Return"), re.I | re.M)
_BUDGET_RE = re.compile(_FIELD.format("Budget"), re.I | re.M)
_KNOWN_RE = re.compile(_FIELD.format("Known"), re.I | re.M)
_NOT_DUP_RE = re.compile(_FIELD.format("Not a duplicate"), re.I | re.M)
_WHY_GENERAL_RE = re.compile(_FIELD.format("Why general"), re.I | re.M)
_TASK_LINE_RE = re.compile(r"^\s*[-*]?\s*\**Task\**\s*:(.*)$", re.I | re.M)

# Default hard budgets in tool calls, by agent type (plugin prefix stripped).
DEFAULT_BUDGETS = {
    "scout": 20, "envoy": 20, "botanist": 15, "landscaper": 40, "Explore": 25,
    "general-purpose": 40, "default": 50,
}
HARD_DENY = 0.9  # task-word overlap at which a repeat is refused outright

_EDIT = re.compile(
    r"\b(implement|fix|edit|write|rewrite|change|modify|refactor|add|create|commit|"
    r"update|rename|delete|remove|patch|migrate|bump|apply)\w*\b", re.I)
_RUN = re.compile(
    r"\b(run|re-?run)\b.*\b(tests?|pytest|ctest|build|lint|suite|benchmark)s?\b|"
    r"\b(pytest|ctest|npm (run )?test|cargo (test|build)|make)\b", re.I)

RETURN_DEFAULT = (
    "Return: answer first, then findings as file:line, then open questions; under 300 "
    "words, no transcript. End with a `Notes:` block (`- <topic> | <finding> | <file>...`) "
    "for durable facts."
)


def budget_line(n: int) -> str:
    return (f"Budget: {n} tool calls (hard limit: further calls are refused; then reply "
            "with what you have, marked partial)")


def short_type(agent_type: str) -> str:
    return (agent_type or "").rsplit(":", 1)[-1]


LEVELS = {"low": 0.5, "medium": 1.0, "high": 2.0, "extreme": 4.0}
MIN_BUDGET, MAX_BUDGET = 5, 400


def level(cfg: dict) -> str:
    """The budget level: plugin option, then MYCELIUM_BUDGET, then config.json."""
    for value in (os.environ.get("CLAUDE_PLUGIN_OPTION_AGENT_BUDGET"),
                  os.environ.get("MYCELIUM_BUDGET"), cfg.get("budget_level")):
        if value and value.strip().lower() in LEVELS:
            return value.strip().lower()
    return "medium"


def default_budget(agent_type: str, cfg: dict | None = None) -> int:
    """Tool calls an agent of this type gets when its brief names no budget:
    the type's default (config `budgets` overrides) scaled by the level."""
    cfg = cfg or {}
    budgets = dict(DEFAULT_BUDGETS, **(cfg.get("budgets") or {}))
    base = int(budgets.get(short_type(agent_type) or "general-purpose", budgets["default"]))
    return min(MAX_BUDGET, max(MIN_BUDGET, round(base * LEVELS[level(cfg)])))


def _task_text(prompt: str) -> str:
    m = _TASK_LINE_RE.search(prompt)
    return m.group(1) if m else prompt


def format_notes(store: Store, notes: list) -> str:
    lines = []
    for n in notes:
        stale = store.stale_paths(n)
        mark = f" (STALE: {', '.join(stale)} changed -- agent must re-verify)" if stale else ""
        lines.append(f"- [{n['id']}] {n['topic']}: {n['text']} (paths: {', '.join(n['paths'])}){mark}")
    return "\n".join(lines)


def specialist(task: str) -> str:
    """The specialist a task fits: landscaper (edits), botanist (runs checks), scout."""
    if _EDIT.search(task):
        return "landscaper"
    if _RUN.search(task):
        return "botanist"
    return "scout"


def agent_name(name: str, repo_root: Path) -> str | None:
    """How the session addresses agent `name`: the plugin's namespaced agent,
    a copy installed by `install-agents`, or None when neither exists."""
    if os.environ.get("CLAUDE_PLUGIN_ROOT"):
        return f"context-garden:{name}"
    for base in (repo_root / ".claude" / "agents", Path.home() / ".claude" / "agents"):
        if (base / f"{name}.md").is_file():
            return name
    return None


def evaluate(store: Store, session_id: str, tool_input: dict, bin_path: str) -> dict:
    """{"deny": str} to refuse, else {"input": dict|None, "context": str}: the
    rewritten tool input (None when unchanged) and a hint for the parent."""
    prompt = tool_input.get("prompt") or ""
    desc = tool_input.get("description") or ""
    cfg = store.config()
    hints = []

    # 4. repeats: refuse only a near-identical one; a resend costs a whole brief.
    if not _NOT_DUP_RE.search(prompt):
        mine = words(_task_text(prompt))
        for prev in store.spawns(session_id):
            sim = jaccard(mine, words(_task_text(prev["prompt"])))
            if sim >= HARD_DENY:
                return {"deny": (
                    f"mycelium: this repeats agent \"{prev['description']}\" spawned earlier this "
                    f"session ({sim:.0%} of the task's words). Reuse its result or continue it "
                    "(SendMessage). Genuinely different? Add a `Not a duplicate: <how>` line."
                )}
            if sim >= cfg["duplicate_threshold"]:
                hints.append(f"resembles agent \"{prev['description']}\" ({sim:.0%}); "
                             "reuse its result if it is the same work")
                break

    # 1. brief: fill what is missing instead of denying.
    add = []
    if not _TASK_RE.search(prompt):
        add.append(f"Task: {desc or 'see below'}")
    if not _KNOWN_RE.search(prompt):
        # 2. known: hand the agent matching notes so it verifies, not rediscovers.
        notes = store.search(desc + "\n" + _task_text(prompt))
        if notes:
            add.append("Known (shared notes; verify, don't rediscover; re-check STALE ones):\n"
                       + format_notes(store, notes))
    tail = []
    if not _RETURN_RE.search(prompt):
        tail.append(RETURN_DEFAULT)

    # 3. route general-purpose to a specialist.
    new_type = None
    current = tool_input.get("subagent_type") or "general-purpose"
    if short_type(current) == "general-purpose" and not _WHY_GENERAL_RE.search(prompt):
        root = getattr(store, "repo_root", None) or getattr(store, "base", Path.cwd())
        name = agent_name(specialist(_task_text(prompt) + " " + desc), root)
        if name:
            new_type = name
            hints.append(f"ran as `{name}` instead of general-purpose (add a `Why general:` "
                         "line to keep general-purpose)")
    if not _BUDGET_RE.search(prompt):
        tail.append(budget_line(default_budget(new_type or current, cfg)))

    new_input = None
    if add or tail or new_type:
        new_input = dict(tool_input)
        parts = (["\n".join(add)] if add else []) + [prompt] + (["\n".join(tail)] if tail else [])
        new_input["prompt"] = "\n\n".join(p for p in parts if p)
        if new_type:
            new_input["subagent_type"] = new_type
    return {"input": new_input, "context": ("mycelium: " + "; ".join(hints)) if hints else ""}
