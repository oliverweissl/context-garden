#!/usr/bin/env python3
"""Per-skill mode: on (Claude uses it automatically), manual (only when the
user calls it, e.g. `/compost <instruction>`), off (not used; the default).

Identical copy in every context-garden skill (skills never import each other).
The mode is stored where Claude Code's own `skillOverrides` setting lives, so
the `/skills` menu and this tool agree:
  on -> "on", manual -> "user-invocable-only", off -> "off" ("name-only" reads as on).
Resolution: $CONTEXT_GARDEN_MODE_<NAME> > <git root>/.claude/settings.local.json
> <git root>/.claude/settings.json > ~/.claude/settings.json > off.
Claude Code applies skillOverrides natively only to standalone skills (not
plugin skills), so SKILL.md also injects `mode --banner` and hooks check
`--is-on`; that makes the mode hold for plugin installs too.

Usage: cg_mode.py <skill> [on|manual|off] [--scope local|project|user]
       cg_mode.py <skill> --banner | --is-on
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

MODES = ("on", "manual", "off")
DEFAULT_MODE = "off"
BIN_DIR = Path(__file__).resolve().parent.parent / "bin"
TO_OVERRIDE = {"on": "on", "manual": "user-invocable-only", "off": "off"}
FROM_OVERRIDE = {"on": "on", "name-only": "on", "user-invocable-only": "manual", "off": "off"}


def repo_root() -> Path:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True,
        )
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip())
    except OSError:
        pass
    return Path.cwd()


def settings_files() -> dict:
    root = repo_root()
    return {
        "local": root / ".claude" / "settings.local.json",
        "project": root / ".claude" / "settings.json",
        "user": Path.home() / ".claude" / "settings.json",
    }


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    return data if isinstance(data, dict) else {}


def resolve(skill: str) -> tuple:
    """(mode, source)."""
    env = os.environ.get(f"CONTEXT_GARDEN_MODE_{skill.upper()}")
    if env in MODES:
        return env, f"$CONTEXT_GARDEN_MODE_{skill.upper()}"
    for scope, path in settings_files().items():
        try:
            value = (_read(path).get("skillOverrides") or {}).get(skill)
        except ValueError:
            continue  # unparseable settings file: Claude Code will complain itself
        if value in FROM_OVERRIDE:
            return FROM_OVERRIDE[value], str(path)
    return DEFAULT_MODE, "default"


def set_mode(skill: str, mode: str, scope: str = "local") -> Path:
    """Write skillOverrides[skill]; every other key of the file is kept as is."""
    path = settings_files()[scope]
    try:
        data = _read(path)
    except ValueError:
        raise SystemExit(f"{path} is not valid JSON; not touching it")
    overrides = data.setdefault("skillOverrides", {})
    overrides[skill] = TO_OVERRIDE[mode]
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(data, indent=2) + "\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def banner(skill: str) -> str:
    mode, _ = resolve(skill)
    if mode == "manual":
        return (
            f"**{skill} is in MANUAL mode here.** Use it only if the user explicitly asked "
            f"for it in this conversation (e.g. typed `/{skill} ...`). Otherwise stop reading "
            "this skill and continue the task without it."
        )
    if mode == "off":
        return (
            f"**{skill} is turned OFF here** (off is the default). Do not use it; continue the "
            f"task without it. Only if the user asks to turn it on: `{BIN_DIR / skill} mode on` "
            "(automatic) or `mode manual` (only when called)."
        )
    return ""


def main(argv: list) -> int:
    if not argv:
        print(__doc__.strip())
        return 2
    skill, rest = argv[0], argv[1:]
    if rest == ["--banner"]:
        text = banner(skill)
        if text:
            print(text)
        return 0
    if rest == ["--is-on"]:
        return 0 if resolve(skill)[0] == "on" else 1
    if not rest:
        mode, source = resolve(skill)
        print(f"{skill}: {mode} (from {source})")
        return 0
    mode, scope = rest[0], "local"
    if len(rest) == 3 and rest[1] == "--scope" and rest[2] in ("local", "project", "user"):
        scope = rest[2]
    elif len(rest) != 1:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    if mode not in MODES:
        print(f"mode must be one of {', '.join(MODES)}", file=sys.stderr)
        return 2
    path = set_mode(skill, mode, scope)
    print(f"{skill}: {mode} (written to {path})")
    now, source = resolve(skill)
    if now != mode:
        print(f"note: still {now} because {source} takes precedence", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
