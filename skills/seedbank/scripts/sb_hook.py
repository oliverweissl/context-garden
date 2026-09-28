"""Claude Code PostToolUse hook: record Read/Grep/Glob as observations.

Reads the hook JSON from stdin ({session_id, cwd, tool_name, tool_input,
tool_response, ...}) and records

    Read  -> observe read <repo-relative file_path>
    Grep  -> observe search "<pattern>[ path=<dir>][ glob=<glob>]"
    Glob  -> observe search "<pattern>[ path=<dir>]"

Contract: never fails or slows the tool call -- always exits 0, prints
nothing on stdout, swallows every error (logged to .seedbank/hook.log when
the store exists). Only acts inside a git work tree; the store is created
lazily on the first recorded observation. Paths outside the repo, and
anything under .git/ or .seedbank/, are skipped.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import time
import traceback
from pathlib import Path

from sb_store import DEFAULT_STORE_DIRNAME, Store

SKIP_TOP_DIRS = (".git", DEFAULT_STORE_DIRNAME)
LOG_NAME = "hook.log"
LOG_MAX_BYTES = 256 * 1024


def git_root(start: Path) -> Path | None:
    """Nearest ancestor containing .git (dir, or file for worktrees) --
    a filesystem walk instead of spawning `git`, to stay well under 100 ms."""
    try:
        d = start.resolve()
    except OSError:
        return None
    for cand in (d, *d.parents):
        if (cand / ".git").exists():
            return cand
    return None


def repo_rel(path: str, cwd: Path, repo_root: Path) -> str | None:
    """Repo-relative posix path, or None if outside the repo / in a skipped dir."""
    p = Path(os.path.expanduser(path))
    if not p.is_absolute():
        p = cwd / p
    try:
        rel = p.resolve().relative_to(repo_root)
    except (OSError, ValueError):
        return None
    if rel.parts and rel.parts[0] in SKIP_TOP_DIRS:
        return None
    return rel.as_posix() or "."


def _search_pattern(tool_input: dict, cwd: Path, repo_root: Path, with_glob: bool) -> str | None:
    pattern = tool_input.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        return None
    label = pattern
    if tool_input.get("path"):
        rel = repo_rel(str(tool_input["path"]), cwd, repo_root)
        if rel is None:
            return None  # searching outside the repo (or inside the store)
        if rel != ".":
            label += f" path={rel}"
    if with_glob and tool_input.get("glob"):
        label += f" glob={tool_input['glob']}"
    return label


def record(data: dict, cwd: Path, repo_root: Path) -> bool:
    import seedbank  # deferred: only paid when there's something to record

    tool = data.get("tool_name")
    tool_input = data.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return False
    if tool == "Read":
        if not isinstance(tool_input.get("file_path"), str):
            return False
        rel = repo_rel(tool_input["file_path"], cwd, repo_root)
        if rel is None or rel == "." or not (repo_root / rel).is_file():
            return False
        handler = seedbank.cmd_observe_read
        ns = argparse.Namespace(
            path=str(repo_root / rel), key=None, task="hook:Read", scope="general"
        )
    elif tool in ("Grep", "Glob"):
        label = _search_pattern(tool_input, cwd, repo_root, with_glob=tool == "Grep")
        if label is None:
            return False
        handler = seedbank.cmd_observe_search
        ns = argparse.Namespace(
            pattern=label, key=None, task=f"hook:{tool}", scope="general", cost=None
        )
    else:
        return False
    store = Store(repo_root / DEFAULT_STORE_DIRNAME, repo_root)
    sid = data.get("session_id")
    store.session = sid if isinstance(sid, str) and sid else seedbank.resolve_session()
    with store.lock():
        handler(ns, store)
    return True


def _log(repo_root: Path | None, message: str) -> None:
    if repo_root is None:
        return
    store_dir = repo_root / DEFAULT_STORE_DIRNAME
    if not store_dir.is_dir():
        return  # don't create a store just to log that nothing happened
    log = store_dir / LOG_NAME
    try:
        if log.exists() and log.stat().st_size > LOG_MAX_BYTES:
            log.replace(log.with_suffix(".log.1"))
        with log.open("a") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {message.rstrip()}\n")
    except OSError:
        pass


def main() -> int:
    repo_root = None
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            data = json.loads(sys.stdin.read() or "null")
            if not isinstance(data, dict):
                raise ValueError(f"hook input is not a JSON object: {type(data).__name__}")
            cwd = Path(data.get("cwd") or os.getcwd())
            repo_root = git_root(cwd)
            if repo_root is not None:
                record(data, cwd, repo_root)
    except BaseException:  # noqa: BLE001 -- the hook must never fail the tool call
        if repo_root is None:
            with contextlib.suppress(BaseException):
                repo_root = git_root(Path.cwd())
        with contextlib.suppress(BaseException):
            _log(repo_root, traceback.format_exc(limit=-2))
    return 0
