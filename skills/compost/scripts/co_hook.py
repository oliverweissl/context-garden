"""Claude Code PreToolUse hook on Bash: route long-output commands through compost.

A command whose program is a known build/test/HPC tool (pytest, ctest, make,
ninja, cmake --build, cargo test, go test, npm test, ...) is not run raw.
What happens is `hook` in <git root>/.compost/config.json, or $COMPOST_HOOK:

  redirect  (default) deny the call; the reason holds the compost command to
            run instead. The re-run goes through the user's normal Bash
            permissions, so the hook never approves anything.
  ask       rewrite the command to `compost run -- ...` and show the user
            the permission prompt for the rewritten command.
  rewrite   rewrite and approve it ("allow"): no extra turn, but it skips
            the permission prompt for the matched command. Opt-in only.
  off       never touch Bash calls.

A leading `cd <dir> &&` into another repo is kept outside the routed command,
so the run lands in that repo's store (sessions started in a folder of repos).
Skipped: commands already using compost, pipes or redirections (the output
is already being limited), backgrounded commands, help/version/collect-only
runs, and any command containing the opt-out marker `# raw`.
Contract: prints nothing (allow untouched) unless it routes the call. Errors
are not swallowed: a crash exits 1 with its traceback on stderr, which Claude
Code shows as a non-blocking hook error (the Bash call still runs).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path

MODES = ("redirect", "ask", "rewrite", "off")
DEFAULT_MODE = "redirect"
OPT_OUT = "# raw"
# Programs whose output is routinely long; matched on the first word of any
# `&&`/`;`-separated segment, after env assignments and `python -m`.
PROGRAMS = {"pytest", "py.test", "ctest", "make", "ninja", "tox", "nox", "sbatch", "srun", "mpirun"}
SUBCOMMANDS = {
    ("cmake", "--build"), ("cargo", "test"), ("cargo", "build"), ("go", "test"), ("go", "build"),
    ("npm", "test"), ("npm", "run"), ("yarn", "test"), ("pnpm", "test"), ("mvn", "test"),
    ("gradle", "test"), ("./gradlew", "test"), ("bazel", "test"), ("bazel", "build"),
}
QUIET_FLAGS = {"-h", "--help", "--version", "--collect-only", "--co", "-n", "--dry-run", "--just-print"}
_UNSAFE = re.compile(r"\|(?!\|)|[<>`]|\$\(|(?<!&)&(?!&)")
_MERGE_STDERR = re.compile(r"\s2>&1\b")


def _words(segment: str) -> list:
    try:
        words = shlex.split(segment)
    except ValueError:
        return []
    while words and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):
        words = words[1:]  # FOO=1 pytest
    if len(words) >= 3 and Path(words[0]).name.startswith("python") and words[1] == "-m":
        words = words[2:]  # python -m pytest
    return words


def matches(command: str) -> bool:
    if not command.strip() or OPT_OUT in command or "compost" in command:
        return False
    if _UNSAFE.search(_MERGE_STDERR.sub(" ", command).replace("||", " ")):
        return False
    hit = False
    for segment in re.split(r"&&|;|\|\|", command):
        words = _words(segment)
        if not words:
            continue
        if QUIET_FLAGS & set(words):
            return False
        prog = Path(words[0]).name if not words[0].startswith("./") else words[0]
        if prog in PROGRAMS or (len(words) > 1 and (prog, words[1]) in SUBCOMMANDS):
            hit = True
    return hit


_LEADING_CD = re.compile(r"""^\s*cd\s+('[^']*'|"[^"]*"|[^\s;&|]+)\s*&&\s*(.+)$""", re.S)


def git_root(start: Path):
    """Nearest ancestor containing .git, or None (filesystem walk: no `git` spawn)."""
    for cand in (start, *start.parents):
        if (cand / ".git").exists():
            return cand
    return None


def routed(command: str, bin_path: str, cwd: str = "") -> str:
    """The compost command to run instead. A leading `cd <dir> &&` into
    another repo (a session started in a folder of repos) stays outside, so
    compost runs in that repo and keeps its runs in that repo's store; the
    explicit --store keeps the printed handles working from anywhere."""
    m = _LEADING_CD.match(command)
    if m and cwd:
        try:
            target = (Path(cwd) / os.path.expanduser(shlex.split(m.group(1))[0])).resolve()
        except (ValueError, IndexError):  # unparsable `cd` argument: don't move it
            target = None
        root = git_root(target) if target is not None and target.is_dir() else None
        if root is not None and root != git_root(Path(cwd).resolve()):
            store = shlex.quote(str(root / ".compost"))
            return (f"cd {m.group(1)} && {shlex.quote(bin_path)} --store {store} "
                    f"run -- bash -c {shlex.quote(m.group(2).strip())}")
    return f"{shlex.quote(bin_path)} run -- bash -c {shlex.quote(command)}"


def mode_for(cwd: str) -> str:
    env = os.environ.get("COMPOST_HOOK")
    if env in MODES:
        return env
    from co_store import find_repo_root  # deferred: the common no-match path stays cheap

    prev = os.getcwd()
    try:
        os.chdir(cwd)
        cfg = json.loads((find_repo_root() / ".compost" / "config.json").read_text())
        mode = cfg.get("hook") if isinstance(cfg, dict) else None
    except FileNotFoundError:  # no config.json (or the cwd is gone): default mode
        mode = None
    finally:
        os.chdir(prev)
    return mode if mode in MODES else DEFAULT_MODE


def decide(payload: dict, bin_path: str):
    """The hook's JSON answer, or None to let the call through untouched."""
    if payload.get("tool_name") != "Bash":
        return None
    tool_input = payload.get("tool_input") or {}
    command = tool_input.get("command")
    if not isinstance(command, str) or tool_input.get("run_in_background") or not matches(command):
        return None
    cwd = payload.get("cwd") or os.getcwd()
    mode = mode_for(cwd)
    if mode == "off":
        return None
    new = routed(command, bin_path, cwd)
    out = {"hookEventName": "PreToolUse"}
    if mode == "redirect":
        out["permissionDecision"] = "deny"
        out["permissionDecisionReason"] = (
            f"compost: this command's output is likely long. Run it as\n  {new}\n"
            "and read the summary (re-runs show only what changed). Need the raw output? "
            f"Append `{OPT_OUT}` to the command."
        )
    else:
        out["permissionDecision"] = "allow" if mode == "rewrite" else "ask"
        out["permissionDecisionReason"] = "compost: routed through the output summarizer"
        out["updatedInput"] = dict(tool_input, command=new)
    return {"hookSpecificOutput": out}


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    bin_path = str(Path(__file__).resolve().parent.parent / "bin" / "compost")
    answer = decide(payload, bin_path) if isinstance(payload, dict) else None
    if answer:
        print(json.dumps(answer))
    return 0


if __name__ == "__main__":
    sys.exit(main())
