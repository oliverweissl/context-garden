"""Orchestrate baseline vs treatment runs for one or more tasks."""

from __future__ import annotations

import contextlib
import shutil
import tempfile
from pathlib import Path

from .fixtures import materialize
from .runners import AgentRunner
from .types import BenchmarkRecord, TaskSpec
from .verify import run_verification


@contextlib.contextmanager
def _workdir(base: Path | None):
    d = Path(tempfile.mkdtemp(prefix="context-garden-bench-", dir=base))
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def run_condition(
    task: TaskSpec,
    condition: str,
    runner: AgentRunner,
    *,
    workdir_base: Path | None = None,
    keep_workdir_to: Path | None = None,
) -> BenchmarkRecord:
    with _workdir(workdir_base) as dest:
        materialize(task, condition, dest)
        result = runner.run(prompt=task.prompt, workdir=dest)
        verification_success, notes = run_verification(task, dest)

        if keep_workdir_to is not None:
            keep_workdir_to.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(dest, keep_workdir_to, dirs_exist_ok=True)

        return BenchmarkRecord(
            task_id=task.task_id,
            component=task.component,
            level=task.level,
            condition=condition,
            model=result.model,
            success=result.success,
            verification_success=verification_success,
            verification_notes=notes,
            input_tokens=result.input_tokens,
            repository_tokens=result.repository_tokens,
            tool_result_tokens=result.tool_result_tokens,
            skill_tokens=result.skill_tokens,
            output_tokens=result.output_tokens,
            tool_calls=result.tool_calls,
            repository_reads=result.repository_reads,
            retries=result.retries,
            runtime=result.runtime_seconds,
            cache_read_tokens=result.cache_read_tokens,
            cost_usd=result.cost_usd,
        )


def run_task(
    task: TaskSpec,
    runner: AgentRunner,
    *,
    repeat: int = 1,
    workdir_base: Path | None = None,
    keep_workdirs_under: Path | None = None,
) -> list[BenchmarkRecord]:
    """Run baseline + treatment for one task, `repeat` times each."""
    records = []
    for condition in ("baseline", "treatment"):
        for i in range(repeat):
            keep_to = (
                keep_workdirs_under / task.task_id / condition / str(i)
                if keep_workdirs_under
                else None
            )
            records.append(
                run_condition(
                    task, condition, runner, workdir_base=workdir_base, keep_workdir_to=keep_to
                )
            )
    return records


def run_all(
    tasks: list[TaskSpec],
    runner: AgentRunner,
    *,
    repeat: int = 1,
    workdir_base: Path | None = None,
    keep_workdirs_under: Path | None = None,
) -> list[BenchmarkRecord]:
    records: list[BenchmarkRecord] = []
    for task in tasks:
        records.extend(
            run_task(
                task,
                runner,
                repeat=repeat,
                workdir_base=workdir_base,
                keep_workdirs_under=keep_workdirs_under,
            )
        )
    return records
