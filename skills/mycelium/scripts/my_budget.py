"""Hard per-agent tool-call budget (PreToolUse on every tool, subagents only).

Claude Code stamps `agent_id`/`agent_type` on a subagent's tool calls (the
main thread's carry neither). Each call of an agent is counted; the limit is
the first number on its brief's `Budget:` line, else a default per agent type
scaled by the budget level (low/medium/high/extreme). Three calls before the
limit the agent gets one wrap-up line; past it every call is refused with
"reply now", so the agent ends with a (partial) report instead of being cut
off by maxTurns or looping on.

Counters live in a temp dir per session (no repo files); one byte per call
appended with O_APPEND, so parallel calls of one agent count correctly.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from my_gate import MAX_BUDGET, MIN_BUDGET, default_budget

WARN_AT = 3  # calls left when the wrap-up line is added
_BUDGET_LINE = re.compile(r"^\s*[-*]?\s*\**Budget\**\s*:\D*?(\d+)", re.I | re.M)


def counter_dir(session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", session_id or "unknown")
    return Path(tempfile.gettempdir()) / "mycelium-budget" / safe


def brief_of(transcript_path: str, agent_id: str) -> str:
    """The agent's brief: the first user message of its own transcript, which
    Claude Code keeps at <session transcript minus .jsonl>/subagents/agent-<id>.jsonl."""
    if not transcript_path:
        return ""
    path = Path(transcript_path).with_suffix("") / "subagents" / f"agent-{agent_id}.jsonl"
    try:
        with path.open() as f:
            for line in f:
                rec = json.loads(line)
                if rec.get("type") != "user":
                    continue
                content = (rec.get("message") or {}).get("content")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    return "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
    except (OSError, ValueError):
        pass
    return ""


def limit_for(payload: dict, cfg: dict) -> int:
    m = _BUDGET_LINE.search(brief_of(payload.get("transcript_path", ""), payload["agent_id"]))
    if m:
        return min(MAX_BUDGET, max(MIN_BUDGET, int(m.group(1))))
    return default_budget(payload.get("agent_type", ""), cfg)


def count_call(payload: dict, cfg: dict) -> tuple:
    """(calls so far including this one, limit) for the calling agent."""
    d = counter_dir(payload.get("session_id", ""))
    if not d.is_dir():
        d.mkdir(parents=True, exist_ok=True)
        prune(d.parent)
    agent = re.sub(r"[^A-Za-z0-9_-]", "_", payload["agent_id"])
    limit_file = d / f"{agent}.limit"
    try:
        limit = int(limit_file.read_text())
    except (OSError, ValueError):
        limit = limit_for(payload, cfg)
        limit_file.write_text(str(limit))
    fd = os.open(d / f"{agent}.calls", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, b".")
        calls = os.fstat(fd).st_size
    finally:
        os.close(fd)
    return calls, limit


def decide(payload: dict, cfg: dict):
    """The hook's answer for one tool call, or None to let it through silently."""
    if not payload.get("agent_id") or cfg.get("gate") == "off":
        return None
    calls, limit = count_call(payload, cfg)
    out = {"hookEventName": "PreToolUse"}
    if calls > limit:
        out["permissionDecision"] = "deny"
        out["permissionDecisionReason"] = (
            f"mycelium: tool budget used up ({limit} calls). Do not call more tools. Reply now "
            "in your Return format with what you have, mark it partial, and list what is "
            "still open."
        )
    elif calls == limit - WARN_AT:
        out["additionalContext"] = (
            f"mycelium: {WARN_AT} tool calls left of {limit}. Finish the current step, then "
            "write your reply."
        )
    else:
        return None
    return {"hookSpecificOutput": out}


def prune(base: Path, max_age: float = 86400) -> None:
    """Drop counters of sessions untouched for a day (a crashed session never
    reaches SubagentStop)."""
    import shutil
    import time

    cutoff = time.time() - max_age
    for d in base.iterdir():
        try:
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def forget(session_id: str, agent_id: str) -> None:
    """Drop an agent's counters once it has stopped."""
    d = counter_dir(session_id)
    agent = re.sub(r"[^A-Za-z0-9_-]", "_", agent_id or "")
    for suffix in (".calls", ".limit"):
        try:
            (d / f"{agent}{suffix}").unlink()
        except OSError:
            pass
