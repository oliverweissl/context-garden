"""Shared data types for the benchmark harness.

See ../README.md for the fixture/task.yaml format and
../../docs/benchmarking.md for the record schema these feed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

VALID_LEVELS = ("file", "repo")
VALID_TREATMENT_MODES = ("skill_available", "preseeded")

# Experimental arms (see docs/benchmarking.md):
#   baseline           no skills installed, original prompt
#   treatment-natural  skills installed, original prompt   -> routing
#   treatment-forced   skills installed, prompt prefixed with an explicit
#                      instruction to use the task's skill(s) -> benefit
# "treatment" is the legacy name for treatment-natural (older records).
ARMS = ("baseline", "treatment-natural", "treatment-forced")
TREATMENT_ARMS = ("treatment", "treatment-natural", "treatment-forced")


@dataclass
class TaskSpec:
    """One loaded benchmarks/fixtures/<task_id>/task.yaml."""

    task_id: str
    component: str
    level: str
    description: str
    prompt: str
    verify: dict[str, Any]
    fixture_dir: Path
    skill_relevance: list[str] = field(default_factory=list)
    base_ref: str = "worktree"
    patch: str | None = None
    overlay: str | None = None
    treatment_mode: str = "skill_available"
    treatment_overlay: str | None = None
    ground_truth: dict[str, Any] = field(default_factory=dict)
    notes: str = ""
    run_by_default: bool = True
    # Optional override of the treatment-forced prompt prefix (default: an
    # instruction to use the skill(s) named in skill_relevance).
    forced_prompt_prefix: str | None = None
    # A second headless session run in the same working copy after `prompt`
    # (e.g. to measure reuse of what the first session recorded); its usage
    # is added to the first's and verification runs after both.
    followup_prompt: str | None = None

    def __post_init__(self) -> None:
        if self.level not in VALID_LEVELS:
            raise ValueError(
                f"{self.task_id}: level must be one of {VALID_LEVELS}, got {self.level!r}"
            )
        if self.base_ref != "worktree":
            raise ValueError(f"{self.task_id}: only base_ref: worktree is currently supported")
        if self.treatment_mode not in VALID_TREATMENT_MODES:
            raise ValueError(
                f"{self.task_id}: treatment_mode must be one of {VALID_TREATMENT_MODES}"
            )
        if self.treatment_mode == "preseeded" and not self.treatment_overlay:
            raise ValueError(
                f"{self.task_id}: treatment_mode 'preseeded' requires treatment_overlay"
            )
        if not self.skill_relevance:
            raise ValueError(f"{self.task_id}: skill_relevance must name at least one skill")


@dataclass
class AgentRunResult:
    """What an AgentRunner reports back for one (task, condition) run."""

    success: bool
    output_text: str = ""
    input_tokens: int = 0
    repository_tokens: int = 0
    tool_result_tokens: int = 0
    skill_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0
    repository_reads: int = 0
    retries: int = 0
    runtime_seconds: float = 0.0
    model: str = ""
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    # Skill names the agent actually invoked (Skill tool / SKILL.md read),
    # when the runner can tell; empty otherwise.
    skill_invoked: list[str] = field(default_factory=list)
    # Non-empty when the session never really ran (CLI error, usage/rate
    # limit, unparseable output): the trial measures the infrastructure,
    # not the agent, so analysis excludes it instead of scoring a FAIL.
    infra_error: str = ""
    # Subagents spawned via the Agent tool and their summed tokens (input +
    # cache creation + output, read from the session's subagents/*.jsonl).
    subagent_count: int = 0
    subagent_tokens: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class BenchmarkRecord:
    """One row of the schema documented in docs/benchmarking.md."""

    task_id: str
    component: str
    level: str
    condition: str  # one of ARMS (or legacy "treatment")
    model: str
    success: bool
    verification_success: bool
    verification_notes: str
    input_tokens: int = 0
    repository_tokens: int = 0
    tool_result_tokens: int = 0
    skill_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0
    repository_reads: int = 0
    retries: int = 0
    runtime: float = 0.0
    # Diagnostic only -- see AgentRunResult.
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    skill_invoked: list[str] = field(default_factory=list)
    infra_error: str = ""
    subagent_count: int = 0
    subagent_tokens: int = 0
    # >1 for fixtures with a followup_prompt (sessions share one working copy).
    sessions: int = 1
    # Scheduling metadata: repetition index within (task, arm), the
    # schedule seed, and this trial's position in the randomised order.
    rep: int = 0
    seed: int | None = None
    order_index: int = -1

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.repository_tokens
            + self.tool_result_tokens
            + self.skill_tokens
            + self.output_tokens
            + self.subagent_tokens
        )

    def to_dict(self) -> dict[str, Any]:
        data = {f: getattr(self, f) for f in self.__dataclass_fields__}
        data["total_tokens"] = self.total_tokens
        return data
