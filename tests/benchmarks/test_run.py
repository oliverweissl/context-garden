"""Offline tests of the trial planner, arm prompts, resumable execution,
and plot entry point (FakeRunner only -- no real agent)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "benchmarks"))

from harness import run as run_mod  # noqa: E402
from harness.analyze import load_records  # noqa: E402
from harness.fixtures import EXCLUDED_PATHS, _is_excluded, discover_tasks  # noqa: E402
from harness.run import (  # noqa: E402
    Trial,
    completed_keys,
    plan_schedule,
    prompt_for,
    read_schedule,
    run_all,
    run_schedule,
)
from harness.types import ARMS, AgentRunResult, BenchmarkRecord  # noqa: E402

TASKS = {t.task_id: t for t in discover_tasks()}


def test_schedule_is_complete_interleaved_and_seeded():
    ids = ["a", "b", "c"]
    s1 = plan_schedule(ids, ARMS, 4, seed=1)
    assert len(s1) == 3 * 3 * 4
    assert len(set(t.key for t in s1)) == len(s1)
    # rep-major blocks: every (task, arm) appears once per block of 9
    for rep in range(4):
        block = s1[rep * 9 : (rep + 1) * 9]
        assert {t.rep for t in block} == {rep}
        assert {(t.task_id, t.arm) for t in block} == {(i, a) for i in ids for a in ARMS}
    assert plan_schedule(ids, ARMS, 4, seed=1) == s1
    assert plan_schedule(ids, ARMS, 4, seed=2) != s1


def test_schedule_rejects_unknown_arm():
    with pytest.raises(ValueError):
        plan_schedule(["a"], ["treatment"], 1, seed=0)


def test_forced_prompt_names_skill_natural_prompt_is_unchanged():
    task = TASKS["pruner-file-tokenusage-bug"]
    assert prompt_for(task, "baseline") == task.prompt
    assert prompt_for(task, "treatment-natural") == task.prompt
    forced = prompt_for(task, "treatment-forced")
    assert forced.endswith(task.prompt)
    assert "`pruner` skill" in forced


def test_preseeded_task_forced_prefix_points_at_agents_md():
    assert "AGENTS.md" in prompt_for(TASKS["seedbank-recurring-facts"], "treatment-forced")


def test_plugin_hooks_are_excluded_from_every_working_copy():
    assert _is_excluded("hooks/hooks.json", EXCLUDED_PATHS)
    assert _is_excluded(".claude-plugin/plugin.json", EXCLUDED_PATHS)


class _Recorder:
    """FakeRunner-alike that records prompts and skips materialization cost."""

    def __init__(self):
        self.prompts: list[str] = []

    def run(self, prompt, workdir):
        self.prompts.append(prompt)
        return AgentRunResult(success=True, output_tokens=10)


@pytest.fixture
def fast(monkeypatch):
    """Skip the (slow, repo-copying) materialize/verify steps."""
    monkeypatch.setattr(run_mod, "materialize", lambda task, cond, dest: dest)
    monkeypatch.setattr(run_mod, "run_verification", lambda task, dest: (True, "ok"))


def test_run_all_writes_schedule_and_records(tmp_path, fast):
    tasks = [TASKS["pruner-file-tokenusage-bug"], TASKS["compost-noisy-failure-cluster"]]
    runner = _Recorder()
    records_path = tmp_path / "run" / "records.jsonl"
    new = run_all(tasks, runner, records_path, runs=2, seed=5)
    assert len(new) == 2 * 3 * 2
    meta, schedule = read_schedule(records_path.parent)
    assert meta["seed"] == 5 and meta["runs"] == 2 and meta["arms"] == list(ARMS)
    rows = load_records(records_path)
    assert [(r["task_id"], r["condition"], r["rep"]) for r in rows] == [t.key for t in schedule]
    assert [r["order_index"] for r in rows] == list(range(len(schedule)))
    assert {r["seed"] for r in rows} == {5}
    assert sum("`pruner` skill" in p for p in runner.prompts) == 2


def test_interrupted_run_resumes_without_repeating(tmp_path, fast):
    tasks = [TASKS["pruner-file-tokenusage-bug"]]
    schedule = plan_schedule([t.task_id for t in tasks], ARMS, 3, seed=0)
    records_path = tmp_path / "records.jsonl"

    class Boom(_Recorder):
        def run(self, prompt, workdir):
            if len(self.prompts) == 4:
                raise KeyboardInterrupt
            return super().run(prompt, workdir)

    with pytest.raises(KeyboardInterrupt):
        run_schedule(tasks, Boom(), schedule, records_path, seed=0)
    assert len(completed_keys(records_path)) == 4
    # simulate a torn final line from a killed process
    with records_path.open("a") as fh:
        fh.write('{"task_id": "pruner-file')

    runner = _Recorder()
    new = run_schedule(tasks, runner, schedule, records_path, seed=0)
    assert len(new) == len(schedule) - 4
    keys = [(r["task_id"], r["condition"], r["rep"]) for r in load_records(records_path)]
    assert sorted(keys) == sorted(t.key for t in schedule)


def test_completed_keys_treats_missing_rep_as_zero(tmp_path):
    path = tmp_path / "records.jsonl"
    path.write_text(json.dumps({"task_id": "t", "condition": "baseline"}) + "\n")
    assert completed_keys(path) == {("t", "baseline", 0)}
    assert Trial("t", "baseline", 0).key in completed_keys(path)


def test_plot_writes_png(tmp_path):
    pytest.importorskip("matplotlib")
    from harness.plot import plot_results_dir

    rows = []
    for arm, passes in (("baseline", 5), ("treatment-natural", 7), ("treatment-forced", 9)):
        for i in range(10):
            rows.append(
                BenchmarkRecord(
                    task_id="pruner-file-tokenusage-bug", component="pruner", level="file",
                    condition=arm, model="m", success=True, verification_success=i < passes,
                    verification_notes="", output_tokens=1000 + 37 * i - 100 * ARMS.index(arm),
                    skill_invoked=["pruner"] if arm != "baseline" and i % 2 else [],
                ).to_dict()
            )  # fmt: skip
    (tmp_path / "records.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    out = plot_results_dir(tmp_path, version="0.2.0")
    assert out.exists() and out.stat().st_size > 0
