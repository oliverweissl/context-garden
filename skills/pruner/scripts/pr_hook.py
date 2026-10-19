"""Claude Code UserPromptSubmit hook: when the prompt carries a traceback,
compiler error or failing-test output, run `select --error` on it and add
the slice to the prompt's context, so the first reads are the right ranges.

Prints the hook's JSON answer or nothing. Never fails or blocks the prompt:
a prompt without an error, a select that errors, or one slower than
SELECT_TIMEOUT seconds (e.g. a first index build of a huge repo) adds
nothing.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

HOOK_BUDGET = 1500
SELECT_TIMEOUT = 20
MAX_PROMPT_CHARS = 50_000
ERROR_PATTERNS = (
    re.compile(r"Traceback \(most recent call last\):"),
    re.compile(r"^\S+\.(?:c|cc|cpp|cxx|h|hh|hpp|hxx)(?::\d+){1,2}: (?:fatal )?error:", re.M),
    re.compile(r"^(?:FAILED|ERROR) \S+\.py::\S+", re.M),
    re.compile(r"^\S+\.py:\d+: in \S+", re.M),
)


def has_error(prompt: str) -> bool:
    return any(p.search(prompt) for p in ERROR_PATTERNS)


def task_line(prompt: str) -> str:
    """The user's own words: the first line that is not error output."""
    for line in prompt.splitlines():
        s = line.strip()
        if s and not s.startswith(("Traceback", "File ", "E ", "FAILED", "ERROR", "  ")) and not has_error(s):
            return s[:200]
    return "Fix the error"


def git_root(start: Path):
    for cand in (start, *start.parents):
        if (cand / ".git").exists():
            return cand
    return None


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
        prompt = data.get("prompt") if isinstance(data, dict) else None
        if not isinstance(prompt, str) or len(prompt) > MAX_PROMPT_CHARS or not has_error(prompt):
            return 0
        root = git_root(Path(data.get("cwd") or os.getcwd()).resolve())
        if root is None:
            return 0
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent / "pruner.py"), "--repo", str(root),
             "select", "--task", task_line(prompt), "--error", prompt, "--budget", str(HOOK_BUDGET)],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=SELECT_TIMEOUT,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return 0
        context = ("pruner (ran automatically on the error in this prompt): read these ranges "
                   "before searching; they are a starting point, not a fence.\n" + proc.stdout.strip())
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                                 "additionalContext": context}}))
    except Exception:  # noqa: BLE001 -- never block or break the prompt
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
