"""AgentRunner implementations: what actually executes a task's prompt.

An AgentRunner takes a prompt and a materialized working directory and
returns an AgentRunResult -- everything else in the harness is agnostic
to which runner produced it.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from .types import AgentRunResult


class AgentRunner(Protocol):
    def run(self, prompt: str, workdir: Path) -> AgentRunResult: ...


class FakeRunner:
    """Deterministic, offline stand-in for a real agent.

    Used by tests/benchmarks/ to validate fixture materialization and
    verification wiring without spending API budget or requiring network
    access -- it is never the subject of an actual benchmark comparison.
    `behavior` receives (prompt, workdir) and must perform whatever file
    edits it wants to simulate the agent having made, then return an
    AgentRunResult (success is just "the agent claims it finished").
    """

    def __init__(self, behavior: Callable[[str, Path], AgentRunResult]) -> None:
        self._behavior = behavior

    def run(self, prompt: str, workdir: Path) -> AgentRunResult:
        return self._behavior(prompt, workdir)


def _local_transcript_path(workdir: Path, session_id: str) -> Path | None:
    """Locate the JSONL session transcript Claude Code writes locally for
    this run, regardless of --output-format -- headless json/stream-json
    only report aggregate usage, but the local transcript still has each
    tool call, so it's the only way to see which cache-creation tokens
    came from reading a Skill's own files vs. everything else. Claude Code
    names the project dir after the cwd with every non-alphanumeric
    character replaced by "-" (not just "/": "_" and "." too)."""
    projects = Path.home() / ".claude" / "projects"
    for cwd in dict.fromkeys((str(workdir.resolve()), str(workdir))):
        encoded = re.sub(r"[^A-Za-z0-9]", "-", cwd)
        path = projects / encoded / f"{session_id}.jsonl"
        if path.is_file():
            return path
    return None


def _tool_use_path(block: dict[str, Any]) -> str | None:
    inp = block.get("input")
    if not isinstance(inp, dict):
        return None
    for key in ("file_path", "path", "notebook_path"):
        value = inp.get(key)
        if isinstance(value, str):
            return value
    return None


def _is_under(path_str: str, root: Path) -> bool:
    try:
        return Path(path_str).resolve().is_relative_to(root)
    except (OSError, ValueError):
        return False


def _skill_tokens_from_transcript(transcript: Path, workdir: Path) -> tuple[int, list[str]]:
    """Best-effort split of cache_creation_input_tokens into Skill content
    (Skill tool invocations, and reads under workdir/.claude/skills/**)
    vs. everything else, plus the names of the skills invoked.

    Heuristic, not exact accounting: a turn's content is split across
    several transcript lines that all repeat the same `usage` block, so
    each assistant `message.id` is counted once; a turn's entire
    cache-creation delta is attributed to skill_tokens if ANY tool result
    feeding it came from a Skill (a turn that reads one Skill file and one
    repo file in parallel over-attributes). Good enough to tell "the
    skill's own files cost roughly N tokens" apart from "exploring the
    repo cost the rest" -- not precise to the token. Returns (0, []) if
    the transcript is missing (older CLI, logging disabled) or no Skill
    was ever used.
    """
    skills_root = (workdir / ".claude" / "skills").resolve()
    tool_use_is_skill: dict[str, bool] = {}
    invoked: list[str] = []
    seen_message_ids: set[str] = set()
    pending_skill_result = False
    total = 0
    try:
        with transcript.open() as fh:
            for line in fh:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = entry.get("message")
                entry_type = entry.get("type")
                if not isinstance(msg, dict) or entry_type not in ("assistant", "user"):
                    continue
                content = msg.get("content")
                if not isinstance(content, list):
                    continue
                if entry_type == "assistant":
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            tool_id = block.get("id")
                            name = _invoked_skill_name(block, skills_root)
                            if name is not None and name not in invoked:
                                invoked.append(name)
                            path = _tool_use_path(block)
                            if tool_id is not None and (name is not None or path is not None):
                                tool_use_is_skill[tool_id] = name is not None or _is_under(
                                    path, skills_root
                                )
                    message_id = msg.get("id")
                    if message_id and message_id not in seen_message_ids:
                        seen_message_ids.add(message_id)
                        if pending_skill_result:
                            usage = msg.get("usage")
                            if isinstance(usage, dict):
                                total += usage.get("cache_creation_input_tokens", 0)
                        pending_skill_result = False
                else:  # user turn: look for tool_result blocks
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            tool_id = block.get("tool_use_id")
                            if tool_use_is_skill.get(tool_id):
                                pending_skill_result = True
    except OSError:
        return 0, []
    return total, invoked


def _invoked_skill_name(block: dict[str, Any], skills_root: Path) -> str | None:
    """The skill a tool_use invokes: a `Skill` tool call's `skill` input,
    or a Read of .claude/skills/<name>/SKILL.md. None otherwise."""
    inp = block.get("input")
    if not isinstance(inp, dict):
        return None
    if block.get("name") == "Skill":
        value = inp.get("skill") or inp.get("command") or inp.get("name")
        return str(value).lstrip("/") if value else "unknown"
    path = _tool_use_path(block)
    if path is not None and Path(path).name == "SKILL.md" and _is_under(path, skills_root):
        return Path(path).resolve().parent.name
    return None


class ClaudeCodeRunner:
    """Invokes the `claude` CLI in headless (--print) mode as the agent.

    This is the actual benchmark subject: baseline and treatment working
    directories are identical except for .claude/skills/ (or a preseeded
    AGENTS.md), so the delta between two ClaudeCodeRunner runs isolates
    the skill's contribution. Running this spends real API usage/cost --
    it is deliberately not exercised by this repository's own test suite
    (see FakeRunner for that). --output-format json's exact payload shape
    can shift across CLI versions; every field read here is via .get()
    with a safe default for that reason.

    Each call appends a unique nonce to the system prompt, so every run's
    prompt-cache key is unique -- no run gets a cheaper/faster ride off a
    prior run's (or a different condition's) already-warmed cache. Turn-
    to-turn caching *within* one run's own agentic tool-call loop is
    untouched (that's real, desired reuse, not cross-run leakage).

    Both conditions run with `--setting-sources project` and
    `--strict-mcp-config`, so user-level settings (enabled plugins and
    their skills, hooks) and user MCP servers don't leak into either run;
    the only skills available are whatever the working copy's own
    .claude/skills/ holds (nothing, for baseline). Skills placed directly
    in ~/.claude/skills are not governed by these flags -- keep that
    directory empty on a benchmark machine.
    """

    def __init__(
        self,
        model: str | None = None,
        permission_mode: str = "acceptEdits",
        max_budget_usd: float = 1.0,
        timeout_seconds: float = 900.0,
        claude_bin: str = "claude",
    ) -> None:
        self.model = model
        self.permission_mode = permission_mode
        self.max_budget_usd = max_budget_usd
        self.timeout_seconds = timeout_seconds
        self.claude_bin = claude_bin

    def run(self, prompt: str, workdir: Path) -> AgentRunResult:
        cmd = [
            self.claude_bin,
            "-p",
            prompt,
            "--output-format",
            "json",
            "--permission-mode",
            self.permission_mode,
            "--permission-prompts",
            "none",
            "--max-budget-usd",
            str(self.max_budget_usd),
            "--append-system-prompt",
            f"benchmark run nonce: {uuid.uuid4()}",
            "--setting-sources",
            "project",
            "--strict-mcp-config",
        ]
        if self.model:
            cmd += ["--model", self.model]

        start = time.monotonic()
        proc = subprocess.run(
            cmd,
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
        )
        runtime = time.monotonic() - start

        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return AgentRunResult(
                success=False,
                output_text=proc.stdout,
                runtime_seconds=runtime,
                raw={"stderr": proc.stderr, "returncode": proc.returncode},
            )

        usage = payload.get("usage", {}) or {}
        # cache_creation_input_tokens: content newly read into context,
        # counted once. NOT cache_read_input_tokens -- that's repeated
        # re-reads of already-counted content across this run's own
        # tool-call turns, which would double/triple/N-count the same
        # material purely as a function of how many turns it took.
        # Real re-read volume is still reported, just as a diagnostic.
        repo_tokens = usage.get("cache_creation_input_tokens", 0)
        skill_tokens = 0
        skill_invoked: list[str] = []
        session_id = payload.get("session_id")
        if session_id:
            transcript = _local_transcript_path(workdir, session_id)
            if transcript is not None:
                skill_tokens, skill_invoked = _skill_tokens_from_transcript(transcript, workdir)
                repo_tokens = max(0, repo_tokens - skill_tokens)

        return AgentRunResult(
            success=(proc.returncode == 0) and not payload.get("is_error", False),
            output_text=payload.get("result", ""),
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            tool_result_tokens=0,
            repository_tokens=repo_tokens,
            skill_tokens=skill_tokens,
            tool_calls=payload.get("num_turns", 0),
            repository_reads=0,
            retries=0,
            runtime_seconds=runtime,
            model=self.model or payload.get("model", ""),
            cache_read_tokens=usage.get("cache_read_input_tokens", 0),
            cost_usd=payload.get("total_cost_usd", 0.0),
            skill_invoked=skill_invoked,
            raw=payload,
        )
