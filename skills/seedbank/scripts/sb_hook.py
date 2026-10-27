"""Claude Code PostToolUse hook: record Read/Grep/Glob as observations.

Reads the hook JSON from stdin ({session_id, cwd, tool_name, tool_input,
tool_response, ...}) and records

    Read  -> observe read <repo-relative file_path>
    Grep  -> observe search "<pattern>[ path=<dir>][ glob=<glob>]"
    Glob  -> observe search "<pattern>[ path=<dir>]"

Contract: never fails or slows the tool call (registered as an async hook
in hooks/hooks.json, so it runs in the background) -- always exits 0, prints
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
import shlex
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


NUDGED_NAME = "nudged.json"
NUDGE_TOP = 3


def stop_main() -> int:
    """Claude Code Stop hook: once per session, if this session produced
    promotion candidates above the threshold, block the stop with a reason
    asking for curation (or an explicit decision not to). Prints the hook's
    JSON answer or nothing; never fails."""
    try:
        data = json.loads(sys.stdin.read() or "null")
        if not isinstance(data, dict) or data.get("stop_hook_active"):
            return 0  # already continuing because of a stop hook: never loop
        session = data.get("session_id")
        repo_root = git_root(Path(data.get("cwd") or os.getcwd()))
        if not isinstance(session, str) or repo_root is None:
            return 0
        store_dir = repo_root / DEFAULT_STORE_DIRNAME
        if not (store_dir / "keystats.json").is_file():
            return 0
        import seedbank  # deferred: only paid inside a repo with a store

        store = Store(store_dir, repo_root)
        with store.lock():
            nudged_path = store_dir / NUDGED_NAME
            try:
                nudged = json.loads(nudged_path.read_text())
            except (OSError, ValueError):
                nudged = {}
            if session in nudged:
                return 0
            keystats = store.load_keystats()
            threshold = store.load_config()["promote_threshold"]
            mine = [c for c in seedbank.build_candidates(store, threshold, False)
                    if session in (keystats.get(c["key"], {}).get("sessions") or [])]
            if not mine:
                return 0
            nudged[session] = time.time()
            keep = sorted(nudged.items(), key=lambda kv: kv[1])[-50:]
            tmp = nudged_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(dict(keep)))
            os.replace(tmp, nudged_path)
        bin_path = shlex.quote(str(Path(__file__).resolve().parent.parent / "bin" / "seedbank"))
        top = ", ".join(f"`{c['key']}`" for c in mine[:NUDGE_TOP])
        print(json.dumps({"decision": "block", "reason": (
            f"seedbank: this session rediscovered {len(mine)} thing(s) worth caching "
            f"(top: {top}). Before finishing, hand them to the `gardener` agent "
            f"(Task: curate seedbank candidates from this session), or run `{bin_path} "
            "candidates` and promote what is worth it yourself. If none is worth keeping, "
            "just finish: this reminder appears once per session."
        )}))
    except BaseException:  # noqa: BLE001 -- the hook must never break stopping
        return 0
    return 0

