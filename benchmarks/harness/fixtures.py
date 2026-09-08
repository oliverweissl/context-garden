"""Discover benchmark fixtures and materialize a working copy for a run.

A fixture is a directory under benchmarks/fixtures/<task_id>/ containing a
task.yaml plus, optionally, a bug.patch (a unified diff applied to a fresh
snapshot of the repository's current working tree) and/or an overlay/
directory (files copied in verbatim, e.g. to add fixture-owned files that
don't exist in the repository at all).

Materializing "baseline" vs "treatment" copies is the whole experiment:
they differ only in whether the relevant Skill(s) are installed under
.claude/skills/ (or, for seedbank's amortized-artifact case, whether a
pre-populated AGENTS.md is present) -- everything else about the two
working copies is identical.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import yaml

from .types import REPO_ROOT, TaskSpec

FIXTURES_DIR = REPO_ROOT / "benchmarks" / "fixtures"


def discover_tasks() -> list[TaskSpec]:
    if not FIXTURES_DIR.is_dir():
        return []
    tasks = [load_task(d) for d in sorted(FIXTURES_DIR.iterdir()) if (d / "task.yaml").exists()]
    return sorted(tasks, key=lambda t: t.task_id)


def load_task(fixture_dir: Path) -> TaskSpec:
    raw = yaml.safe_load((fixture_dir / "task.yaml").read_text(encoding="utf-8")) or {}
    required = {"task_id", "component", "level", "description", "prompt", "verify"}
    missing = required - raw.keys()
    if missing:
        raise ValueError(f"{fixture_dir}/task.yaml missing required field(s): {sorted(missing)}")
    return TaskSpec(fixture_dir=fixture_dir, **raw)


def _copy_worktree(dest: Path) -> None:
    """Snapshot the repository's current working tree into dest.

    Uses `git ls-files` (tracked + untracked-but-not-gitignored) rather
    than `git archive <ref>`, so fixtures materialize correctly even
    against uncommitted work -- and dest never contains a .git directory,
    which keeps `git apply` below from picking up REPO_ROOT's repo state.
    """
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(REPO_ROOT),
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        capture_output=True,
        check=True,
    )
    for rel in proc.stdout.decode("utf-8").split("\0"):
        if not rel:
            continue
        src = REPO_ROOT / rel
        if not src.is_file():
            continue
        dst = dest / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _apply_patch(dest: Path, patch_path: Path) -> None:
    subprocess.run(
        ["git", "apply", "--whitespace=nowarn", str(patch_path.resolve())],
        cwd=dest,
        check=True,
    )


def _copy_overlay(overlay_dir: Path, dest: Path) -> None:
    for src in overlay_dir.rglob("*"):
        if src.is_dir():
            continue
        rel = src.relative_to(overlay_dir)
        dst = dest / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _install_skill(name: str, dest: Path) -> None:
    src = REPO_ROOT / "skills" / name
    if not src.is_dir():
        raise ValueError(f"no such skill: {name} (looked in {src})")
    dst = dest / ".claude" / "skills" / name
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def materialize(task: TaskSpec, condition: str, dest: Path) -> Path:
    """Build a working copy of the repo for (task, condition) at dest.

    dest must already exist and be empty; the caller owns its lifecycle
    (see run.py, which uses a fresh temp directory per condition so
    baseline and treatment never share state).
    """
    if condition not in ("baseline", "treatment"):
        raise ValueError(f"condition must be 'baseline' or 'treatment', got {condition!r}")

    _copy_worktree(dest)

    if task.patch:
        _apply_patch(dest, task.fixture_dir / task.patch)
    if task.overlay:
        _copy_overlay(task.fixture_dir / task.overlay, dest)

    if condition == "treatment":
        if task.treatment_mode == "skill_available":
            for name in task.skill_relevance:
                _install_skill(name, dest)
        elif task.treatment_mode == "preseeded":
            _copy_overlay(task.fixture_dir / task.treatment_overlay, dest)

    return dest
