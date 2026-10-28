#!/usr/bin/env python3
"""Plugin-level onboarding for context-garden: show and set every skill's mode
at once, pick a preset by use case, and tell a fresh install how to start.

Not a skill: it ships only with the plugin and drives the skills' own
`cg_mode.py` (identical in every skill), so modes resolve exactly as each
skill resolves them.

Usage: garden.py status
       garden.py presets
       garden.py preset <name> [--scope local|project|user]
       garden.py set <skill> <on|manual|off> [--scope local|project|user]
       garden.py hook session-start
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SKILLS = ("seedbank", "pruner", "compost", "mycelium", "greenhouse", "weeder")
SCOPES = ("local", "project", "user")

# Use case -> skills turned on. Every skill not listed is set to manual, so it
# still answers `/context-garden:<skill> ...` but never runs on its own.
PRESETS = {
    "solo": (
        "One repository, everyday coding and debugging",
        ("seedbank", "pruner", "compost"),
    ),
    "platform": (
        "Big project split over several repos that call each other (services, shared packages)",
        ("greenhouse", "seedbank", "pruner", "compost"),
    ),
    "swarm": (
        "Tasks where Claude spawns many subagents (parallel search, investigate, edit)",
        ("mycelium", "seedbank", "pruner", "compost"),
    ),
    "platform-swarm": (
        "Many agents working across a multi-repo platform",
        ("greenhouse", "mycelium", "seedbank", "pruner", "compost"),
    ),
    "logs": (
        "Long builds, test suites, CI or HPC/SLURM logs; nothing else",
        ("compost",),
    ),
    "all": ("Everything on, including weeder", SKILLS),
    "off": ("Everything off (silences the setup notice)", ()),
}


def _cg_mode():
    """Load one skill's cg_mode.py (all copies are identical)."""
    path = PLUGIN_ROOT / "skills" / "seedbank" / "scripts" / "cg_mode.py"
    spec = importlib.util.spec_from_file_location("cg_mode", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def status() -> str:
    cg = _cg_mode()
    rows = []
    for skill in SKILLS:
        mode, source = cg.resolve(skill)
        rows.append(f"  {skill:<11} {mode:<7} ({source})")
    return "context-garden skill modes:\n" + "\n".join(rows)


def presets() -> str:
    lines = ["Presets (python3 garden.py preset <name> [--scope local|project|user]):"]
    for name, (use, on) in PRESETS.items():
        turned_on = ", ".join(on) if on else "nothing"
        lines.append(f"  {name:<15} {use}\n  {'':<15} on: {turned_on}")
    lines.append("Skills a preset does not turn on are set to manual (only when you call them),")
    lines.append("except with 'off'.")
    return "\n".join(lines)


def apply_preset(name: str, scope: str) -> str:
    cg = _cg_mode()
    _, on = PRESETS[name]
    for skill in SKILLS:
        mode = "on" if skill in on else ("off" if name == "off" else "manual")
        cg.set_mode(skill, mode, scope)
    return f"Applied preset '{name}' ({scope} scope).\n" + status()


def is_unconfigured() -> bool:
    """True if no skill has a mode set anywhere (fresh install)."""
    cg = _cg_mode()
    return all(cg.resolve(skill)[1] == "default" for skill in SKILLS)


def session_start_notice() -> str:
    if not is_unconfigured():
        return ""
    user = (
        "context-garden is installed, but every skill is off until you pick some. "
        "Run /context-garden:setup to choose by use case (one repo, multi-repo platform, "
        "many subagents, long logs), or /context-garden:setup off to keep them off and hide "
        "this notice."
    )
    claude = (
        "context-garden plugin: all its skills are off (fresh install, nothing configured). "
        "If the user asks how to use or turn on context-garden, point them to "
        "/context-garden:setup. Do not use the skills until they are turned on."
    )
    return json.dumps({
        "systemMessage": user,
        "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": claude},
    })


def _scope(rest: list) -> str | None:
    if not rest:
        return "local"
    if len(rest) == 2 and rest[0] == "--scope" and rest[1] in SCOPES:
        return rest[1]
    return None


def main(argv: list) -> int:
    cmd, rest = (argv[0], argv[1:]) if argv else ("", [])
    if cmd == "hook":
        if rest != ["session-start"]:
            return 0
        # A crash exits 1 with its traceback: a non-blocking hook error. Never 2 here.
        if os.environ.get("CLAUDE_PROJECT_DIR"):
            os.chdir(os.environ["CLAUDE_PROJECT_DIR"])
        text = session_start_notice()
        if text:
            print(text)
        return 0
    if cmd == "status" and not rest:
        print(status())
        return 0
    if cmd == "presets" and not rest:
        print(presets())
        return 0
    if cmd == "preset" and rest and rest[0] in PRESETS and _scope(rest[1:]):
        print(apply_preset(rest[0], _scope(rest[1:])))
        return 0
    if cmd == "set" and len(rest) >= 2 and rest[0] in SKILLS and _scope(rest[2:]):
        cg = _cg_mode()
        if rest[1] not in cg.MODES:
            print(f"mode must be one of {', '.join(cg.MODES)}", file=sys.stderr)
            return 2
        path = cg.set_mode(rest[0], rest[1], _scope(rest[2:]))
        print(f"{rest[0]}: {rest[1]} (written to {path})")
        return 0
    if cmd == "preset" and rest and rest[0] not in PRESETS:
        print(f"unknown preset '{rest[0]}'\n\n{presets()}", file=sys.stderr)
        return 2
    print(__doc__.strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
