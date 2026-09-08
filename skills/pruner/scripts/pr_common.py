"""Shared utilities: token estimation, hashing, and repo file discovery."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

IGNORE_DIRS = {
    ".git",
    ".pruner",
    "__pycache__",
    "node_modules",
    "build",
    "dist",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    ".idea",
    ".vscode",
    "cmake-build-debug",
    "cmake-build-release",
    ".tox",
    ".seedbank",
    ".compost",
}
IGNORE_SUFFIXES = {".pyc", ".so", ".o", ".a", ".dylib", ".dll", ".exe"}
MAX_FILE_BYTES = 2_000_000

PYTHON_SUFFIXES = {".py"}
CPP_SUFFIXES = {".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh"}
CONFIG_NAMES = {
    "cmakelists.txt",
    "makefile",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements.txt",
    "package.json",
    "tox.ini",
    "pytest.ini",
    "conftest.py",
    "dockerfile",
}
CONFIG_SUFFIXES = {".yaml", ".yml", ".toml", ".ini", ".cfg"}
DOC_SUFFIXES = {".md", ".rst", ".txt"}


def estimate_tokens(text: str) -> int:
    return max(1, round(len(text) / 4))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def classify_file(rel_path: str) -> str:
    p = Path(rel_path)
    name = p.name.lower()
    suffix = p.suffix.lower()
    if name in CONFIG_NAMES or suffix in CONFIG_SUFFIXES:
        return "config"
    if suffix in DOC_SUFFIXES:
        return "doc"
    stem = p.stem.lower()
    parts = {seg.lower() for seg in p.parts}
    if suffix in PYTHON_SUFFIXES or suffix in CPP_SUFFIXES:
        if stem.startswith("test_") or stem.endswith("_test") or stem.endswith("test"):
            return "test"
        if "tests" in parts or "test" in parts:
            return "test"
    if suffix in PYTHON_SUFFIXES:
        return "python"
    if suffix in CPP_SUFFIXES:
        return "cpp"
    return "other"


def _git_tracked_files(repo_root: Path) -> list[str] | None:
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return [line for line in out.stdout.decode(errors="replace").splitlines() if line.strip()]


def discover_files(repo_root: Path) -> list[str]:
    """Return repo-relative paths of files worth indexing. Prefers `git
    ls-files` (respects .gitignore for free); falls back to a walk with a
    hardcoded ignore list when not in a git repo."""
    tracked = _git_tracked_files(repo_root)
    if tracked is not None:
        candidates = tracked
    else:
        candidates = []
        for path in repo_root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in IGNORE_DIRS for part in path.relative_to(repo_root).parts):
                continue
            candidates.append(str(path.relative_to(repo_root)))

    out = []
    for rel in candidates:
        full = repo_root / rel
        if full.suffix.lower() in IGNORE_SUFFIXES:
            continue
        if any(part in IGNORE_DIRS for part in Path(rel).parts):
            continue
        try:
            if full.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        out.append(rel)
    return sorted(out)


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="strict")
    except (UnicodeDecodeError, OSError):
        return None


def changed_files_from_git(repo_root: Path) -> list[str]:
    try:
        out = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    return [
        line.strip() for line in out.stdout.decode(errors="replace").splitlines() if line.strip()
    ]
