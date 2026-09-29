"""Delegation gate: decide whether an Agent call may run as written.

Checked in order; the first failing rule denies (or warns, in "warn" mode):
  1. brief   -- prompt has `Task:`, `Why delegate:`, `Return:` and `Budget:` lines
  2. repeat  -- not a near-duplicate of an agent already spawned this session
                (unless a `Not a duplicate:` line says why)
  3. known   -- if fresh notes match the task, the prompt carries a `Known:` line
                (paste the relevant notes, or write `Known: none relevant`)
A denial's reason is what the model reads, so it states the fix exactly.
"""

from __future__ import annotations

import re

from my_store import Store, jaccard, words

REQUIRED_FIELDS = ("Task", "Why delegate", "Return", "Budget")
_FIELD_RE = {f: re.compile(rf"^\s*[-*]?\s*\**{re.escape(f)}\**\s*:", re.I | re.M) for f in REQUIRED_FIELDS}
_KNOWN_RE = re.compile(r"^\s*[-*]?\s*\**Known\**\s*:", re.I | re.M)
_NOT_DUP_RE = re.compile(r"^\s*[-*]?\s*\**Not a duplicate\**\s*:", re.I | re.M)
_TASK_LINE_RE = re.compile(r"^\s*[-*]?\s*\**Task\**\s*:(.*)$", re.I | re.M)


def _task_words(prompt: str) -> set:
    m = _TASK_LINE_RE.search(prompt)
    return words(m.group(1) if m else prompt)


def format_notes(store: Store, notes: list) -> str:
    lines = []
    for n in notes:
        stale = store.stale_paths(n)
        mark = f" (STALE: {', '.join(stale)} changed -- agent must re-verify)" if stale else ""
        lines.append(f"- [{n['id']}] {n['topic']}: {n['text']} (paths: {', '.join(n['paths'])}){mark}")
    return "\n".join(lines)


def evaluate(store: Store, session_id: str, tool_input: dict, bin_path: str) -> tuple:
    """(verdict, message): verdict is "allow" or "deny"; message explains a denial."""
    prompt = tool_input.get("prompt") or ""
    desc = tool_input.get("description") or ""

    missing = [f for f in REQUIRED_FIELDS if not _FIELD_RE[f].search(prompt)]
    if missing:
        return "deny", (
            "mycelium delegation gate: agent brief is missing "
            + ", ".join(f"`{f}:`" for f in missing)
            + ". First ask: is this a single lookup, a small edit or one test command? "
            "Then do it yourself instead of delegating. Otherwise re-issue with one line each:\n"
            "Task: <one bounded question or change, with a finish condition>\n"
            "Why delegate: <why the main thread would be less efficient>\n"
            "Return: <compact format: findings with file:line, answer, unresolved questions>\n"
            "Budget: <max searches/turns; when exhausted return partial findings>"
        )

    cfg = store.config()
    if not _NOT_DUP_RE.search(prompt):
        mine = _task_words(prompt)
        for prev in store.spawns(session_id):
            sim = jaccard(mine, _task_words(prev["prompt"]))
            if sim >= cfg["duplicate_threshold"]:
                return "deny", (
                    f"mycelium delegation gate: this repeats agent \"{prev['description']}\" "
                    f"spawned earlier this session ({sim:.0%} overlap). Reuse its result, or "
                    "continue that agent (SendMessage) instead of re-running the investigation. "
                    "If the task is genuinely different, add a `Not a duplicate: <how>` line."
                )

    if not _KNOWN_RE.search(prompt):
        notes = store.search(desc + "\n" + prompt)
        if notes:
            return "deny", (
                "mycelium delegation gate: shared notes already cover part of this task:\n"
                + format_notes(store, notes)
                + "\nRe-issue with a `Known:` line carrying the relevant notes (tell the agent "
                "to verify, not rediscover, and to re-check any STALE note), or "
                "`Known: none relevant`. Record new findings afterwards with "
                f"`{bin_path} note \"<topic>\" \"<finding>\" <file>...`."
            )
    return "allow", ""
