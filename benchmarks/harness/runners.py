"""AgentRunner implementations: what actually executes a task's prompt.

An AgentRunner takes a prompt and a materialized working directory and
returns an AgentRunResult -- everything else in the harness is agnostic
to which runner produced it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from .types import AgentRunResult

# Skill modes (skills/*/scripts/cg_mode.py) also read ~/.claude/settings.json,
# so a mode set on the benchmark machine would silently change the treatment.
SKILLS_ON_ENV = {
    f"CONTEXT_GARDEN_MODE_{d.name.upper()}": "on"
    for d in (Path(__file__).resolve().parents[2] / "skills").iterdir()
    if (d / "SKILL.md").is_file()
}


class AgentRunner(Protocol):
    def run(self, prompt: str, workdir: Path) -> AgentRunResult: ...


class FakeRunner:
    """Deterministic, offline stand-in for a real agent (tests only).
    `behavior(prompt, workdir)` makes the simulated edits and returns an
    AgentRunResult.
    """

    def __init__(self, behavior: Callable[[str, Path], AgentRunResult]) -> None:
        self._behavior = behavior

    def run(self, prompt: str, workdir: Path) -> AgentRunResult:
        return self._behavior(prompt, workdir)


def _local_transcript_path(workdir: Path, session_id: str) -> Path | None:
    """Locate this run's local JSONL session transcript -- the only source of
    per-tool-call usage (headless json output is aggregate only). Claude
    Code names the project dir after the cwd with every non-alphanumeric
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


def _subagent_usage(transcript: Path) -> tuple[int, int]:
    """(count, tokens) over <session>/subagents/agent-*.jsonl next to the main
    transcript. Each assistant message.id is counted once (a turn's lines
    repeat its usage block)."""
    count = tokens = 0
    for path in sorted((transcript.parent / transcript.stem / "subagents").glob("agent-*.jsonl")):
        count += 1
        seen: set[str] = set()
        try:
            lines = path.read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                msg = json.loads(line).get("message")
            except json.JSONDecodeError:
                continue
            if not isinstance(msg, dict) or not isinstance(msg.get("usage"), dict):
                continue
            mid = msg.get("id")
            if mid in seen:
                continue
            if mid:
                seen.add(mid)
            u = msg["usage"]
            tokens += sum(
                int(u.get(k) or 0)
                for k in ("input_tokens", "cache_creation_input_tokens", "output_tokens")
            )
    return count, tokens


def _skill_tokens_from_transcript(transcript: Path, workdir: Path) -> tuple[int, list[str]]:
    """Best-effort split of cache_creation_input_tokens into Skill content
    (Skill tool invocations, and reads under workdir/.claude/skills/**)
    vs. everything else, plus the names of the skills invoked.

    Heuristic, not exact accounting: a turn's content is split across
    several transcript lines that all repeat the same `usage` block, so
    each assistant `message.id` is counted once; a turn's entire
    cache-creation delta is attributed to skill_tokens if ANY tool result
    feeding it came from a Skill (a turn that reads one Skill file and one
    repo file in parallel over-attributes). Returns (0, []) if
    the transcript is missing (older CLI, logging disabled) or no Skill
    was ever used.
    """
    skills_root = (workdir / ".claude" / "skills").resolve()
    tool_use_is_skill: dict[str, bool] = {}
    invoked: list[str] = []
    skill_call: dict[str, str] = {}
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
                            if name is not None and tool_id is not None:
                                skill_call[tool_id] = name
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
                            # a denied/failed Skill call loaded nothing: not invoked
                            name = skill_call.get(tool_id)
                            if name and not block.get("is_error") and name not in invoked:
                                invoked.append(name)
                            if tool_use_is_skill.get(tool_id) and not block.get("is_error"):
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
    Spends real API budget, so the test suite never runs it. The json
    payload shape shifts across CLI versions, hence .get() with defaults.

    A per-call nonce in the system prompt makes every run's prompt-cache key
    unique (no cross-run cache warming; in-run turn caching is unaffected).
    `--setting-sources project --strict-mcp-config` keeps user plugins,
    hooks and MCP servers out of both arms -- but not ~/.claude/skills,
    which must be empty on a benchmark machine.
    """

    def __init__(
        self,
        model: str | None = None,
        permission_mode: str = "acceptEdits",
        max_budget_usd: float = 1.0,
        timeout_seconds: float = 900.0,
        claude_bin: str = "claude",
        allowed_tools: tuple[str, ...] = ("Bash", "Skill"),
    ) -> None:
        self.model = model
        # acceptEdits alone auto-denies Bash and Skill in headless runs, so
        # allow them explicitly (safe: each run is in a throwaway temp worktree).
        self.allowed_tools = allowed_tools
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
        if self.allowed_tools:
            cmd += ["--allowedTools", *self.allowed_tools]
        if self.model:
            cmd += ["--model", self.model]

        start = time.monotonic()
        proc = subprocess.run(
            cmd,
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            env={**os.environ, **SKILLS_ON_ENV},
        )
        runtime = time.monotonic() - start

        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return AgentRunResult(
                success=False,
                output_text=proc.stdout,
                runtime_seconds=runtime,
                infra_error=f"unparseable output (rc={proc.returncode}): "
                + (proc.stderr or proc.stdout)[-500:],
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
        skill_tokens = subagent_count = subagent_tokens = 0
        skill_invoked: list[str] = []
        session_id = payload.get("session_id")
        if session_id:
            transcript = _local_transcript_path(workdir, session_id)
            if transcript is not None:
                skill_tokens, skill_invoked = _skill_tokens_from_transcript(transcript, workdir)
                repo_tokens = max(0, repo_tokens - skill_tokens)
                subagent_count, subagent_tokens = _subagent_usage(transcript)

        infra_error = ""
        if not usage.get("output_tokens") and not usage.get("input_tokens"):
            # The model never produced a turn (usage/rate limit, auth, CLI
            # error): an infra failure, not an agent failure.
            infra_error = f"no model usage: {str(payload.get('result', ''))[:300]}"

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
            infra_error=infra_error,
            subagent_count=subagent_count,
            subagent_tokens=subagent_tokens,
            raw=payload,
        )
