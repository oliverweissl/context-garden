"""Claude Code UserPromptSubmit hook: when the prompt carries a traceback,
compiler error or failing-test output, run `select --error` on it and add
the slice to the prompt's context, so the first reads are the right ranges.

Prints the hook's JSON answer or nothing. Never blocks the prompt: a prompt
without an error, or a select slower than SELECT_TIMEOUT seconds (e.g. a
first index build of a huge repo), adds nothing. A select that fails, or a
crash here, exits 1 with the error on stderr, which Claude Code shows as a
non-blocking hook error. A session started in a folder of repos uses the
repo the error's file paths point into.
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
MAX_PATH_CHARS = 1024
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


_ERROR_PATH = re.compile(r"""(?:File "([^"]+)"|^\s*([^\s:"']+\.(?:py|c|cc|cpp|cxx|h|hh|hpp|hxx))[:(])""", re.M)


def repo_from_error(prompt: str, cwd: Path):
    """For a session started in a folder of repos: the repo the error's file
    paths point into (absolute, workspace-relative like `repo/src/x.py`, or
    repo-relative like `src/x.py` when exactly one child repo has the file)."""
    votes = {}
    children = [c for c in sorted(cwd.iterdir()) if (c / ".git").exists()] if cwd.is_dir() else []
    for m in _ERROR_PATH.finditer(prompt):
        text = m.group(1) or m.group(2)
        if len(text) > MAX_PATH_CHARS:
            continue  # not a path (exists() would raise ENAMETOOLONG)
        raw = Path(text).expanduser()
        if raw.is_absolute():
            hits = [git_root(raw.parent)] if raw.exists() else []
        elif (cwd / raw).exists():
            hits = [git_root((cwd / raw).resolve().parent)]
        else:
            hits = [c for c in children if (c / raw).is_file()]
            hits = hits if len(hits) == 1 else []
        for root in hits:
            if root is not None:
                votes[root] = votes.get(root, 0) + 1
    return max(votes, key=votes.get) if votes else None


def main() -> int:
    data = json.loads(sys.stdin.read() or "{}")
    prompt = data.get("prompt") if isinstance(data, dict) else None
    if not isinstance(prompt, str) or len(prompt) > MAX_PROMPT_CHARS or not has_error(prompt):
        return 0
    cwd = Path(data.get("cwd") or os.getcwd()).resolve()
    root = git_root(cwd) or repo_from_error(prompt, cwd)
    if root is None:
        return 0
    try:
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent / "pruner.py"), "--repo", str(root),
             "select", "--task", task_line(prompt), "--error", prompt, "--budget", str(HOOK_BUDGET)],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=SELECT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return 0
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr or f"pruner select exited {proc.returncode}\n")
        return 1
    if not proc.stdout.strip():
        return 0
    context = ("pruner (ran automatically on the error in this prompt): read these ranges "
               "before searching; they are a starting point, not a fence.\n")
    if root != git_root(cwd):
        context += f"Paths are relative to the repo {root}\n"
    context += proc.stdout.strip()
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                             "additionalContext": context}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
