"""Offline tests of the scripts/benchmark CLI: argument validation and
--resume restoring the stored runner config (no real agent is ever run)."""

from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "benchmarks"))

from harness.fixtures import discover_tasks  # noqa: E402
from harness.run import plan_schedule, write_schedule  # noqa: E402


def _load_cli():
    path = REPO_ROOT / "scripts" / "benchmark"
    loader = importlib.machinery.SourceFileLoader("benchmark_cli", str(path))
    spec = importlib.util.spec_from_loader("benchmark_cli", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


cli = _load_cli()


class _NoRunner:
    def __init__(self, **kwargs):
        pass

    def run(self, **kwargs):  # pragma: no cover -- must never be reached
        raise AssertionError("runner must not be invoked")


def _resumable(tmp_path: Path, task_ids: list[str], **extra) -> Path:
    schedule = plan_schedule(task_ids, ["baseline"], 1, seed=3)
    write_schedule(tmp_path, schedule, seed=3, arms=["baseline"], runs=1, **extra)
    return tmp_path


@pytest.mark.parametrize("runs", ["0", "-2"])
def test_runs_below_one_is_a_usage_error(runs, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["run", "--dry-run", "--runs", runs])
    assert exc.value.code == 2
    assert "must be >= 1" in capsys.readouterr().err


STORED = {
    "runner": "claude-code",
    "model": "m-stored",
    "permission_mode": "plan",
    "max_budget_usd": 2.5,
}


def test_resume_restores_stored_runner_config(tmp_path, capsys):
    task_id = discover_tasks()[0].task_id
    run_dir = _resumable(tmp_path, [task_id], runner_config=STORED)
    assert cli.main(["run", "--resume", str(run_dir), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "runner config restored" in out
    assert "max $2.50/session" in out


def test_resume_accepts_matching_and_rejects_differing_flags(tmp_path, capsys):
    task_id = discover_tasks()[0].task_id
    run_dir = _resumable(tmp_path, [task_id], runner_config=STORED)
    ok = ["run", "--resume", str(run_dir), "--dry-run", "--model", "m-stored"]
    assert cli.main(ok) == 0
    capsys.readouterr()
    bad = ["run", "--resume", str(run_dir), "--dry-run", "--max-budget-usd", "9"]
    assert cli.main(bad) == 1
    err = capsys.readouterr().err
    assert "--max-budget-usd=9.0" in err and "stored: 2.5" in err


def test_fresh_run_config_is_flags_over_defaults():
    args = argparse.Namespace(runner=None, model="m1", permission_mode=None, max_budget_usd=None)
    config = cli._resolve_runner_config(args, None)
    assert config == {**cli.RUNNER_DEFAULTS, "model": "m1"}


def test_resume_with_unknown_task_errors_cleanly(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "ClaudeCodeRunner", _NoRunner)
    run_dir = _resumable(tmp_path, ["no-such-task"], runner_config=STORED)
    assert cli.main(["run", "--resume", str(run_dir)]) == 1
    assert "unknown task(s): ['no-such-task']" in capsys.readouterr().err
