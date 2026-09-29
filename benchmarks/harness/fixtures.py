"""Discover benchmark fixtures and materialize a working copy for a run.

A fixture is a directory under benchmarks/fixtures/<task_id>/ containing a
task.yaml plus, optionally, a bug.patch (a unified diff applied to a fresh
snapshot of the repository's current working tree) and/or an overlay/
directory (files copied in verbatim, e.g. to add fixture-owned files that
don't exist in the repository at all).

Baseline and treatment copies differ only in .claude/skills/ (or a
preseeded AGENTS.md); both treatment arms share one copy and differ only
in the prompt (see run.prompt_for).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import yaml

from .types import ARMS, REPO_ROOT, TREATMENT_ARMS, TaskSpec

FIXTURES_DIR = REPO_ROOT / "benchmarks" / "fixtures"

# Never copied into either arm: anything that leaks the answer (fixture
# ground truth, prior results, harness tests that script the correct
# answer, benchmark docs, demo examples), the repo's own agent context
# (AGENTS.md/CLAUDE.md, seedbank warm files -- would pre-seed baseline),
# local .claude/ config, and plugin manifest/hooks (no arm may pick up a
# plugin hook from the working copy).
EXCLUDED_PATHS = (
    ".claude/",
    ".claude-plugin/",
    "hooks/",
    ".seedbank/",
    ".mycelium/",
    "AGENTS.md",
    "CLAUDE.md",
    "benchmarks/fixtures/",
    "benchmarks/results/",
    "benchmarks/README.md",
    "docs/benchmarking.md",
    "examples/",
    "tests/benchmarks/test_harness.py",
    "tests/benchmarks/test_analyze.py",
    "tests/benchmarks/test_run.py",
    "tests/benchmarks/test_verify.py",
)


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


def _is_excluded(rel: str, excluded: tuple[str, ...]) -> bool:
    return any(rel == e.rstrip("/") or (e.endswith("/") and rel.startswith(e)) for e in excluded)


def _copy_worktree(dest: Path, excluded: tuple[str, ...] = EXCLUDED_PATHS) -> None:
    """Snapshot the repository's current working tree into dest, minus
    `excluded` paths (a trailing "/" excludes a whole directory).

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
        if not rel or _is_excluded(rel, excluded):
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
    if condition not in ("baseline", *TREATMENT_ARMS):
        raise ValueError(f"condition must be one of {ARMS}, got {condition!r}")

    # Exclude the skill(s) under test from BOTH conditions' repo snapshot,
    # so baseline can't discover and run skills/<name>/ directly; other
    # skills stay as ordinary repository content (e.g. seedbank's facts
    # live in skills/weeder/SKILL.md).
    skill_paths = tuple(f"skills/{name}/" for name in task.skill_relevance)
    _copy_worktree(dest, EXCLUDED_PATHS + skill_paths)

    if task.patch:
        _apply_patch(dest, task.fixture_dir / task.patch)
    if task.overlay:
        _copy_overlay(task.fixture_dir / task.overlay, dest)

    if condition in TREATMENT_ARMS:
        if task.treatment_mode == "skill_available":
            for name in task.skill_relevance:
                _install_skill(name, dest)
            if task.treatment_overlay:  # e.g. .claude/settings.json enabling the skill's hooks
                _copy_overlay(task.fixture_dir / task.treatment_overlay, dest)
        elif task.treatment_mode == "preseeded":
            _copy_overlay(task.fixture_dir / task.treatment_overlay, dest)

    return dest
