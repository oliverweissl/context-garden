#!/usr/bin/env python3
"""MCP server (stdio) that lets agent terminals started by the Context Garden
app work together: a manager prompts and reads its workers, workers report back.

It only forwards calls to the app's local control endpoint, named by the
CG_CONTROL / CG_TOKEN environment variables the app sets in each agent
terminal. Outside the app those are missing and the server offers no tools.
Who may reach whom is decided by the app (the manager -> worker links drawn in
its Orchestrate view), never here. Standard library only.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

PROTOCOL = "2025-06-18"
SERVER = {"name": "garden-control", "version": "0.6.0"}

INSTRUCTIONS = (
    "You run inside Context Garden. Other agent terminals of this garden may be "
    "linked to you in the app: list_terminals shows your workers (terminals you "
    "manage) and your managers. "
    "If you manage workers: give each a bounded task with send_prompt, check on "
    "them with read_output, and combine their results; workers answer with "
    "report_to_manager, which arrives in your terminal as a message starting "
    "'[Report from your worker'. Do not poll in a tight loop: send the tasks, "
    "carry on or wait for the reports. "
    "If you are a worker (a message starts '[Task from your manager'): do the "
    "task, then call report_to_manager with what you did, what changed and "
    "anything blocking you."
)

TOOLS = [
    {
        "name": "list_terminals",
        "description": (
            "Agent terminals in this garden that you manage (workers) or that "
            "manage you (managers), and whether each is running."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "send_prompt",
        "description": (
            "Give a task to one of your workers: the text is typed into its "
            "terminal as a prompt. Only works for terminals you manage."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": "The worker's key or title, from list_terminals.",
                },
                "text": {
                    "type": "string",
                    "description": "The task. Be specific: goal, folder, what to return.",
                },
            },
            "required": ["to", "text"],
        },
    },
    {
        "name": "read_output",
        "description": (
            "Read the last lines of a worker's terminal screen to see its "
            "progress. Only works for terminals you manage."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "from": {"type": "string", "description": "The worker's key or title."},
                "lines": {
                    "type": "integer",
                    "description": "How many lines, default 60, at most 400.",
                },
            },
            "required": ["from"],
        },
    },
    {
        "name": "report_to_manager",
        "description": (
            "Send a short report (result, changes, blockers) to the terminal "
            "that manages you. It appears there as a message."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "The report."}},
            "required": ["text"],
        },
    },
]


def enabled() -> bool:
    return bool(os.environ.get("CG_CONTROL") and os.environ.get("CG_TOKEN"))


def call_app(tool: str, args: dict) -> dict:
    body = json.dumps({"token": os.environ["CG_TOKEN"], "tool": tool, "args": args}).encode()
    req = urllib.request.Request(
        os.environ["CG_CONTROL"].rstrip("/") + "/call",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read())
        except Exception:
            return {"ok": False, "error": f"Context Garden answered {e.code}"}
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"ok": False, "error": f"cannot reach Context Garden: {e}"}


def tool_result(text: str, error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": error}


def handle(msg: dict) -> dict | None:
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:  # a notification: nothing to answer
        return None
    if method == "initialize":
        version = (msg.get("params") or {}).get("protocolVersion") or PROTOCOL
        result = {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER,
            "instructions": INSTRUCTIONS if enabled() else "",
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS if enabled() else []}
    elif method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        if not enabled() or name not in {t["name"] for t in TOOLS}:
            result = tool_result(
                "not available: this terminal was not started by Context Garden", True
            )
        else:
            out = call_app(name, params.get("arguments") or {})
            if out.get("ok"):
                result = tool_result(json.dumps(out.get("result"), indent=2))
            else:
                result = tool_result(str(out.get("error") or "failed"), True)
    else:
        error = {"code": -32601, "message": f"unknown method {method}"}
        return {"jsonrpc": "2.0", "id": mid, "error": error}
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
