#!/usr/bin/env python3
"""MCP server (stdio): read-only query tools of the context-garden skills, as typed
tool calls with plain results (callable as functions from Claude Code's and Codex's
code mode, no Bash quoting or Bash permission prompts).

Each tool runs the skill's own CLI (skills/<name>/scripts/<name>.py) in the session's
working directory and returns its output; nothing here reimplements a skill. A tool
answers only when its skill is *on* for that repository (`<skill> mode on`, the same
switch the skill's hooks honour); manual or off gives a short "not enabled" error.
Needs no environment, works outside the Context Garden app. Standard library only.
Separate from garden-control (mcp/cg_control.py), which is app-only.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# Child processes get no console window of their own on Windows.
_NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}

PROTOCOL = "2025-06-18"
SERVER = {"name": "garden-tools", "version": "0.7.0"}
SKILLS = Path(__file__).resolve().parent.parent / "skills"
MAX_OUT = 60_000  # characters returned per call
TIMEOUT = 60

INSTRUCTIONS = (
    "Read-only lookups into context-garden's per-repository memory: seedbank_facts "
    "(cached repo facts), greenhouse_brief (what other repos of the platform know), "
    "pruner_select (smallest relevant file ranges for a task), compost_get and "
    "compost_grep (raw lines of a stored command run), mycelium_notes (findings "
    "from earlier subagents). A tool is available only when its skill is on in "
    "the repository (`<skill> mode on`)."
)


def _str(desc: str) -> dict:
    return {"type": "string", "description": desc}


def _int(desc: str) -> dict:
    return {"type": "integer", "description": desc}


def _tool(name: str, description: str, props: dict, required: list[str]) -> dict:
    return {
        "name": name,
        "description": description,
        "inputSchema": {"type": "object", "properties": props, "required": required},
    }


TOOLS = [
    _tool(
        "seedbank_facts",
        "Cached facts about this repository (build commands, conventions, invariants, past "
        "mistakes): the compiled AGENTS.md plus the seedbank status line.",
        {},
        [],
    ),
    _tool(
        "greenhouse_brief",
        "What the other repositories of this platform know that is relevant to a task.",
        {"task": _str("What you are about to do."), "budget": _int("Max tokens (approx.).")},
        ["task"],
    ),
    _tool(
        "pruner_select",
        "Smallest sufficient set of file:line ranges for a task in this repository (JSON).",
        {
            "task": _str("The bug, error or change."),
            "budget": _int("Token budget for the slice."),
            "changed": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Changed file paths, if known.",
            },
            "error": _str("Raw compiler error or traceback text."),
        },
        ["task"],
    ),
    _tool(
        "compost_get",
        "Raw lines of a stored compost run (see the run id in a compost summary).",
        {"run_id": _str("Run id."), "lines": _str("N, A:B (inclusive) or 'all'.")},
        ["run_id", "lines"],
    ),
    _tool(
        "compost_grep",
        "Regex (or literal) search in the raw output of a stored compost run.",
        {
            "run_id": _str("Run id."),
            "pattern": _str("Regex, or literal with fixed=true."),
            "fixed": {"type": "boolean", "description": "Treat pattern as a literal string."},
            "context": _int("Lines of context."),
            "max": _int("Max matches."),
        },
        ["run_id", "pattern"],
    ),
    _tool(
        "mycelium_notes",
        "Findings that earlier agents recorded for this repository that match a query.",
        {"query": _str("What you are looking for."), "limit": _int("Max notes.")},
        ["query"],
    ),
]


def _opt(args: dict, key: str, flag: str) -> list[str]:
    v = args.get(key)
    return [flag, str(v)] if v not in (None, "") else []


def command(tool: str, a: dict) -> tuple[str, list[str]]:
    """(skill, CLI argv) for a tool call; KeyError when an argument is missing."""
    if tool == "seedbank_facts":
        return "seedbank", ["status"]
    if tool == "greenhouse_brief":
        return "greenhouse", ["brief", *_opt(a, "budget", "--budget"), a["task"]]
    if tool == "pruner_select":
        argv = [
            "select",
            "--json",
            "--task",
            a["task"],
            *_opt(a, "budget", "--budget"),
            *_opt(a, "error", "--error"),
        ]
        if a.get("changed"):
            argv += ["--changed", *map(str, a["changed"])]
        return "pruner", argv
    if tool == "compost_get":
        return "compost", ["get", "--lines", str(a["lines"]), str(a["run_id"])]
    if tool == "compost_grep":
        argv = [
            "grep",
            *(["-F"] if a.get("fixed") else []),
            *_opt(a, "context", "--context"),
            *_opt(a, "max", "--max"),
            str(a["run_id"]),
            str(a["pattern"]),
        ]
        return "compost", argv
    if tool == "mycelium_notes":
        return "mycelium", ["brief", *_opt(a, "limit", "--limit"), a["query"]]
    raise KeyError(tool)


def skill_is_on(skill: str) -> bool:
    r = subprocess.run(
        [sys.executable, str(SKILLS / skill / "scripts" / "cg_mode.py"), skill, "--is-on"],
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        **_NO_WINDOW,
    )
    return r.returncode == 0


def agents_md() -> str:
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, **_NO_WINDOW)
    root = Path(r.stdout.strip()) if r.returncode == 0 else Path.cwd()
    f = root / "AGENTS.md"
    return (
        f.read_text(errors="replace")
        if f.is_file()
        else "(no AGENTS.md yet: no facts promoted in this repository)"
    )


def run_tool(tool: str, args: dict) -> tuple[str, bool]:
    """(text, is_error)."""
    try:
        skill, argv = command(tool, args)
    except KeyError as e:
        return f"missing or unknown argument: {e}", True
    try:
        if not skill_is_on(skill):
            return (
                f"the {skill} skill is not on in this repository (run `{skill} mode on`, "
                "or ask the user); use the Bash CLI if you were told to call it explicitly"
            ), True
        r = subprocess.run(
            [sys.executable, str(SKILLS / skill / "scripts" / f"{skill}.py"), *argv],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            **_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return f"{skill} failed to run: {e}", True
    out = (r.stdout or "").rstrip()
    if tool == "seedbank_facts":
        out = f"{out}\n\n{agents_md()}".strip()
    if r.returncode != 0:
        return ((r.stderr or out or f"{skill} exited {r.returncode}").strip()[:MAX_OUT]), True
    if len(out) > MAX_OUT:
        out = out[:MAX_OUT] + f"\n[truncated at {MAX_OUT} characters]"
    return out or "(no output)", False


def tool_result(text: str, error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": error}


def handle(msg: dict) -> dict | None:
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        return None
    if method == "initialize":
        version = (msg.get("params") or {}).get("protocolVersion") or PROTOCOL
        result = {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER,
            "instructions": INSTRUCTIONS,
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        p = msg.get("params") or {}
        if p.get("name") not in {t["name"] for t in TOOLS}:
            result = tool_result(f"unknown tool {p.get('name')!r}", True)
        else:
            result = tool_result(*run_tool(p["name"], p.get("arguments") or {}))
    else:
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "error": {"code": -32601, "message": f"unknown method {method}"},
        }
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        reply = handle(msg)
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
