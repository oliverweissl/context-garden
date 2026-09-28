"""Plan and execute benchmark trials: tasks x arms x repetitions.

A *trial* is one (task, arm, rep) triple. `plan_schedule` lays all trials
out in a randomised, interleaved order fixed by a seed: repetition-major
blocks, each block holding every (task, arm) pair once in shuffled order.
Arms therefore alternate throughout the run instead of running back to
back, so time-varying effects (API latency, model-side changes, rate
limits) hit every arm roughly equally.

`run_schedule` executes a schedule, appending each record to
records.jsonl as soon as it finishes, and skips any (task, arm, rep)
already present there -- an interrupted run resumes where it stopped.
"""

from __future__ import annotations

import contextlib
import json
import random
import shutil
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .fixtures import materialize
from .runners import AgentRunner
from .types import ARMS, BenchmarkRecord, TaskSpec
from .verify import run_verification

SCHEDULE_FILE = "schedule.json"
RECORDS_FILE = "records.jsonl"


@dataclass(frozen=True)
class Trial:
    task_id: str
    arm: str
    rep: int

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.task_id, self.arm, self.rep)


def forced_prefix(task: TaskSpec) -> str:
    if task.forced_prompt_prefix:
        return task.forced_prompt_prefix.strip()
    names = ", ".join(f"`{n}`" for n in task.skill_relevance)
    noun = "skill" if len(task.skill_relevance) == 1 else "skills"
    return f"Use the {names} {noun} (installed under .claude/skills/) for this task."


def prompt_for(task: TaskSpec, arm: str) -> str:
    """The prompt an arm sends: identical for baseline and
    treatment-natural; treatment-forced prefixes an explicit instruction
    to use the task's skill(s)."""
    if arm == "treatment-forced":
        return f"{forced_prefix(task)}\n\n{task.prompt}"
    return task.prompt


def plan_schedule(
    task_ids: Iterable[str], arms: Iterable[str], runs: int, seed: int
) -> list[Trial]:
    task_ids, arms = list(task_ids), list(arms)
    unknown = [a for a in arms if a not in ARMS]
    if unknown:
        raise ValueError(f"unknown arm(s) {unknown}; choose from {ARMS}")
    if runs < 1:
        raise ValueError("runs must be >= 1")
    rng = random.Random(seed)
    schedule: list[Trial] = []
    for rep in range(runs):
        block = [Trial(t, a, rep) for t in task_ids for a in arms]
        rng.shuffle(block)
        schedule.extend(block)
    return schedule


def write_schedule(
    run_dir: Path, schedule: list[Trial], *, seed: int, arms: list[str], runs: int, **extra: Any
) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / SCHEDULE_FILE
    payload = {
        "seed": seed,
        "arms": list(arms),
        "runs": runs,
        "tasks": sorted({t.task_id for t in schedule}),
        **extra,
        "order": [asdict(t) for t in schedule],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def read_schedule(run_dir: Path) -> tuple[dict[str, Any], list[Trial]]:
    payload = json.loads((run_dir / SCHEDULE_FILE).read_text(encoding="utf-8"))
    return payload, [Trial(**t) for t in payload["order"]]


def completed_keys(records_path: Path) -> set[tuple[str, str, int]]:
    """(task_id, arm, rep) of every record already in records.jsonl.
    A truncated trailing line (process killed mid-write) is ignored."""
    done: set[tuple[str, str, int]] = set()
    if not records_path.exists():
        return done
    for line in records_path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        done.add((row["task_id"], row["condition"], int(row.get("rep", 0))))
    return done


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
    rep: int = 0,
    seed: int | None = None,
    order_index: int = -1,
    workdir_base: Path | None = None,
    keep_workdir_to: Path | None = None,
) -> BenchmarkRecord:
    with _workdir(workdir_base) as dest:
        materialize(task, condition, dest)
        result = runner.run(prompt=prompt_for(task, condition), workdir=dest)
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
            skill_invoked=list(result.skill_invoked),
            rep=rep,
            seed=seed,
            order_index=order_index,
        )


def run_schedule(
    tasks: list[TaskSpec],
    runner: AgentRunner,
    schedule: list[Trial],
    records_path: Path,
    *,
    seed: int | None = None,
    workdir_base: Path | None = None,
    keep_workdirs_under: Path | None = None,
    on_record: Callable[[int, int, BenchmarkRecord], None] | None = None,
) -> list[BenchmarkRecord]:
    """Execute every not-yet-completed trial in schedule order, appending
    each record to records_path immediately. Returns the new records."""
    by_id = {t.task_id: t for t in tasks}
    missing = sorted({t.task_id for t in schedule} - by_id.keys())
    if missing:
        raise ValueError(f"schedule names unknown task(s): {missing}")
    records_path.parent.mkdir(parents=True, exist_ok=True)
    done = completed_keys(records_path)
    # A process killed mid-write can leave a torn last line; terminate it so
    # the next appended record starts on its own line (load skips the torn one).
    if records_path.exists() and records_path.stat().st_size:
        with records_path.open("rb+") as fh:
            fh.seek(-1, 2)
            if fh.read(1) != b"\n":
                fh.write(b"\n")
    new: list[BenchmarkRecord] = []
    for index, trial in enumerate(schedule):
        if trial.key in done:
            continue
        keep_to = (
            keep_workdirs_under / trial.task_id / trial.arm / str(trial.rep)
            if keep_workdirs_under
            else None
        )
        record = run_condition(
            by_id[trial.task_id],
            trial.arm,
            runner,
            rep=trial.rep,
            seed=seed,
            order_index=index,
            workdir_base=workdir_base,
            keep_workdir_to=keep_to,
        )
        with records_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record.to_dict()) + "\n")
        done.add(trial.key)
        new.append(record)
        if on_record is not None:
            on_record(index, len(schedule), record)
    return new


def run_all(
    tasks: list[TaskSpec],
    runner: AgentRunner,
    records_path: Path,
    *,
    arms: Iterable[str] = ARMS,
    runs: int = 10,
    seed: int = 0,
    workdir_base: Path | None = None,
    keep_workdirs_under: Path | None = None,
) -> list[BenchmarkRecord]:
    """Plan + persist a schedule next to records_path, then execute it."""
    arms = list(arms)
    schedule = plan_schedule([t.task_id for t in tasks], arms, runs, seed)
    write_schedule(records_path.parent, schedule, seed=seed, arms=arms, runs=runs)
    return run_schedule(
        tasks,
        runner,
        schedule,
        records_path,
        seed=seed,
        workdir_base=workdir_base,
        keep_workdirs_under=keep_workdirs_under,
    )
