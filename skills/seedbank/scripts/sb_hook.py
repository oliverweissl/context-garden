"""Claude Code PostToolUse hook: record Read/Grep/Glob/Bash as observations.

Reads the hook JSON from stdin ({session_id, cwd, tool_name, tool_input,
tool_response, ...}) and records

    Read  -> observe read <repo-relative file_path>
    Grep  -> observe search "<pattern>[ path=<dir>][ glob=<glob>]"
    Glob  -> observe search "<pattern>[ path=<dir>]"
    Bash  -> the same, for read-only commands: cat/head/tail/sed/awk/... <file>
             count as reads; grep/rg/ag/git grep and find -name as searches
             (keys match the Read/Grep/Glob ones). `cd <dir>` is followed.

Contract: never slows the tool call (registered as an async hook in
hooks/hooks.json, so it runs in the background) and prints nothing on stdout.
Errors are not swallowed: an uncaught exception exits 1 with its traceback on
stderr, which Claude Code shows as a non-blocking hook error. The repo is resolved per call from the file or directory
the tool touched (falling back to the session's cwd), so a session started
in a folder of many repos still records into each repo's own store; the
store is created lazily on the first recorded observation. Paths outside
any git work tree, and anything under .git/ or .seedbank/, are skipped.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import itertools
import json
import os
import re
import shlex
import sys
import time
from pathlib import Path

from sb_store import DEFAULT_STORE_DIRNAME, Store

SKIP_TOP_DIRS = (".git", DEFAULT_STORE_DIRNAME)


def git_root(start: Path) -> Path | None:
    """Nearest ancestor containing .git (dir, or file for worktrees) --
    a filesystem walk instead of spawning `git`, to stay well under 100 ms."""
    d = start.resolve()
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
    except ValueError:
        return None
    if rel.parts and rel.parts[0] in SKIP_TOP_DIRS:
        return None
    return rel.as_posix() or "."


def _target(path: str | None, cwd: Path) -> tuple:
    """(repo root, repo-relative path) of a path the tool touched -- the repo
    is the one holding the path, not the session's -- or (None, None) when it
    is outside any repo or inside a skipped dir. No path: the cwd's repo."""
    p = Path(os.path.expanduser(path)) if path else cwd
    if not p.is_absolute():
        p = cwd / p
    root = git_root(p if p.is_dir() else p.parent)
    if root is None:
        return None, None
    rel = repo_rel(str(p), cwd, root)
    return (root, rel) if rel is not None else (None, None)


def _search(pattern, path: str | None, glob, cwd: Path) -> tuple | None:
    if not isinstance(pattern, str) or not pattern:
        return None
    root, rel = _target(path, cwd)
    if root is None:
        return None  # searching outside a repo (or inside the store)
    label = pattern
    if rel != ".":
        label += f" path={rel}"
    if glob:
        label += f" glob={glob}"
    return root, "search", label


def _read(path, cwd: Path) -> tuple | None:
    if not isinstance(path, str) or not path:
        return None
    root, rel = _target(path, cwd)
    if root is None or rel == "." or not (root / rel).is_file():
        return None
    return root, "read", rel


# --- read-only Bash commands -------------------------------------------------
READ_CMDS = {"cat", "head", "tail", "less", "more", "bat", "nl", "sed", "awk"}
SEARCH_CMDS = {"grep", "egrep", "fgrep", "rg", "ag", "ack"}
# grep/rg flags that take a separate value (the glob-ish ones are kept for the label)
VALUE_FLAGS = {"-A", "-B", "-C", "-m", "-e", "-f", "-g", "-t", "-T", "-d", "-D", "--glob",
               "--type", "--include", "--exclude", "--max-count", "--context", "--regexp"}
GLOB_FLAGS = {"-g", "--glob", "--include"}
SEPARATORS = {"&&", "||", ";", "|", "&", "|&"}


def _segments(command: str) -> list:
    """[(words, follows_pipe)] per simple command; redirections dropped.
    Commands with substitutions are skipped whole (can't know what they read);
    a heredoc ends the command (its body is data, not arguments)."""
    if "$(" in command or "`" in command:
        return []
    if "<<" in command:
        command = command.split("\n", 1)[0]
    lex = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    try:
        tokens = list(lex)
    except ValueError:
        return []
    out, words, piped, skip = [], [], False, False
    for tok in tokens + [";"]:
        if tok in SEPARATORS:
            if words:
                out.append((words, piped))
            words, piped, skip = [], tok in ("|", "|&"), False
        elif skip:
            skip = False  # the redirection's target (a file, or the fd in `>&1`)
        elif set(tok) <= set("<>&"):
            skip = True
            if words and words[-1].isdigit():
                words.pop()  # the fd in `2>`
        else:
            words.append(tok)
    return out


def _grep_parts(words: list) -> tuple:
    """(pattern, paths, glob) of a grep/rg command line (after the program)."""
    pattern, paths, glob = None, [], None
    it = iter(words)
    for w in it:
        if w == "--":
            continue
        if w.startswith("--") and "=" in w:
            flag, val = w.split("=", 1)
            if flag in GLOB_FLAGS:
                glob = val
            elif flag == "--regexp":
                pattern = val
        elif w in VALUE_FLAGS:
            val = next(it, None)
            if w in GLOB_FLAGS:
                glob = val
            elif w in ("-e", "--regexp"):
                pattern = val
        elif w.startswith("-") and w != "-":
            continue
        elif pattern is None:
            pattern = w
        else:
            paths.append(w)
    return pattern, paths, glob


def bash_observations(command: str, cwd: Path) -> list:
    """(root, kind, value) for each file read / search in a Bash command."""
    out = []
    for words, piped in _segments(command):
        while words and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):
            words = words[1:]  # FOO=1 cmd
        if not words:
            continue
        prog, args = Path(words[0]).name, words[1:]
        if prog == "cd":
            target = Path(os.path.expanduser(args[0])) if args else Path.home()
            cwd = target if target.is_absolute() else cwd / target
        elif prog == "git" and args[:1] == ["grep"] and not piped:
            pattern, paths, _ = _grep_parts(args[1:])
            out += [_search(pattern, p, None, cwd) for p in paths or [None]]
        elif prog in SEARCH_CMDS and not piped:
            pattern, paths, glob = _grep_parts(args)
            out += [_search(pattern, p, glob, cwd) for p in paths or [None]]
        elif prog == "find" and not piped:
            roots = list(itertools.takewhile(lambda a: not a.startswith("-") and a not in "(!", args))
            names = [args[i + 1] for i, a in enumerate(args[:-1])
                     if a in ("-name", "-iname", "-path", "-ipath")]
            if names:
                out += [_search(names[0], r, None, cwd) for r in roots or [None]]
        elif prog in READ_CMDS and not (prog == "sed" and any(a.startswith("-i") for a in args)):
            out += [_read(a, cwd) for a in args if not a.startswith("-")]
    return [o for o in out if o is not None]


def observations(data: dict, cwd: Path) -> list:
    tool = data.get("tool_name")
    tool_input = data.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return []
    if tool == "Read":
        found = [_read(tool_input.get("file_path"), cwd)]
    elif tool in ("Grep", "Glob"):
        found = [_search(tool_input.get("pattern"), tool_input.get("path"),
                         tool_input.get("glob") if tool == "Grep" else None, cwd)]
    elif tool == "Bash" and isinstance(tool_input.get("command"), str):
        found = bash_observations(tool_input["command"], cwd)
    else:
        return []
    return [f for f in found if f is not None]


def record(data: dict, cwd: Path) -> int:
    """Record every observation of this tool call into its repo's store."""
    found = observations(data, cwd)
    if not found:
        return 0
    import seedbank  # deferred: only paid when there's something to record

    tool = data.get("tool_name")
    sid = data.get("session_id")
    session = sid if isinstance(sid, str) and sid else seedbank.resolve_session()
    for root, kind, value in found:
        if kind == "read":
            handler = seedbank.cmd_observe_read
            ns = argparse.Namespace(path=str(root / value), key=None, task=f"hook:{tool}",
                                    scope="general")
        else:
            handler = seedbank.cmd_observe_search
            ns = argparse.Namespace(pattern=value, key=None, task=f"hook:{tool}",
                                    scope="general", cost=None)
        store = Store(root / DEFAULT_STORE_DIRNAME, root)
        store.session = session
        with store.lock():
            handler(ns, store)
    return len(found)


def main() -> int:
    with contextlib.redirect_stdout(io.StringIO()):
        data = json.loads(sys.stdin.read() or "null")
        if not isinstance(data, dict):
            raise ValueError(f"hook input is not a JSON object: {type(data).__name__}")
        cwd = Path(data.get("cwd") or os.getcwd())
        record(data, cwd)
    return 0


NUDGED_NAME = "nudged.json"
NUDGE_TOP = 3
RECENT_STORE_SECS = 7 * 86400


def session_stores(cwd: Path) -> list:
    """Repos whose store this session may have written to: the cwd's repo,
    plus -- when the session was started in a folder of repos rather than a
    repo ($CLAUDE_PROJECT_DIR not in a git work tree) -- every child repo
    with a recently updated store, since the record hook files observations
    under the repo each tool call touched."""
    roots = []
    here = git_root(cwd)
    if here is not None:
        roots.append(here)
    base = Path(os.environ.get("CLAUDE_PROJECT_DIR") or cwd)
    if git_root(base) is None:
        cutoff = time.time() - RECENT_STORE_SECS
        for ks in sorted(base.glob(f"*/{DEFAULT_STORE_DIRNAME}/keystats.json")):
            with contextlib.suppress(FileNotFoundError):  # store removed since the glob
                if ks.stat().st_mtime >= cutoff and ks.parent.parent not in roots:
                    roots.append(ks.parent.parent)
    return [r for r in roots if (r / DEFAULT_STORE_DIRNAME / "keystats.json").is_file()]


def _session_candidates(repo_root: Path, session: str) -> list:
    """This session's candidates in one store, marking the store as nudged;
    [] if there are none or this session was already nudged there."""
    import seedbank  # deferred: only paid inside a repo with a store

    store_dir = repo_root / DEFAULT_STORE_DIRNAME
    store = Store(store_dir, repo_root)
    with store.lock():
        nudged_path = store_dir / NUDGED_NAME
        try:
            nudged = json.loads(nudged_path.read_text())
        except FileNotFoundError:
            nudged = {}
        if session in nudged:
            return []
        keystats = store.load_keystats()
        threshold = store.load_config()["promote_threshold"]
        mine = [c for c in seedbank.build_candidates(store, threshold, False)
                if session in (keystats.get(c["key"], {}).get("sessions") or [])]
        if not mine:
            return []
        nudged[session] = time.time()
        keep = sorted(nudged.items(), key=lambda kv: kv[1])[-50:]
        tmp = nudged_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(dict(keep)))
        os.replace(tmp, nudged_path)
    return mine


def stop_main() -> int:
    """Claude Code Stop hook: once per session, if this session produced
    promotion candidates above the threshold, block the stop with a reason
    asking for curation (or an explicit decision not to). Prints the hook's
    JSON answer or nothing."""
    data = json.loads(sys.stdin.read() or "null")
    if not isinstance(data, dict) or data.get("stop_hook_active"):
        return 0  # already continuing because of a stop hook: never loop
    session = data.get("session_id")
    if not isinstance(session, str):
        return 0
    cwd = Path(data.get("cwd") or os.getcwd())
    here = git_root(cwd)
    found = []
    for root in session_stores(cwd):
        mine = _session_candidates(root, session)
        if mine:
            found.append((root, mine))
    if not found:
        return 0
    bin_path = shlex.quote(str(Path(__file__).resolve().parent.parent / "bin" / "seedbank"))
    total = sum(len(m) for _, m in found)
    def top(mine):
        return "(top: " + ", ".join(f"`{c['key']}`" for c in mine[:NUDGE_TOP]) + ")"

    if len(found) == 1 and found[0][0] == here:
        where, run = top(found[0][1]), f"`{bin_path} candidates`"
    else:
        where = "in " + "; ".join(f"{root} {top(mine)}" for root, mine in found)
        run = f"`cd <repo> && {bin_path} candidates` in each"
    print(json.dumps({"decision": "block", "reason": (
        f"seedbank: this session rediscovered {total} thing(s) worth caching {where}. "
        "Before finishing, hand them to the `gardener` agent (Task: curate seedbank "
        f"candidates from this session, naming the repo), or run {run} and promote what "
        "is worth it yourself. If none is worth keeping, just finish: this reminder "
        "appears once per session."
    )}))
    return 0
