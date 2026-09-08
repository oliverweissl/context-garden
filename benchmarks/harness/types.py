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
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class BenchmarkRecord:
    """One row of the schema documented in docs/benchmarking.md."""

    task_id: str
    component: str
    level: str
    condition: str  # "baseline" | "treatment"
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

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.repository_tokens
            + self.tool_result_tokens
            + self.skill_tokens
            + self.output_tokens
        )

    def to_dict(self) -> dict[str, Any]:
        data = {f: getattr(self, f) for f in self.__dataclass_fields__}
        data["total_tokens"] = self.total_tokens
        return data
