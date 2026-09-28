"""Deterministic post-run verification.

Verification never trusts the agent's own claim of success -- it inspects
the materialized working copy (and, for `weeder_audit`, calls the
repository's own weeder CLI) after the run completes. `verify` in
task.yaml is one of:

  type: pytest
    args: [<pytest args, relative to the working copy>]

  type: file_contains
    path: <path relative to the working copy>
    contains: [<substring>, ...]   # ALL must be present
    matches: [<regex>, ...]        # optional; ALL must re.search the file

  type: answer_key
    path: <path relative to the working copy>
    key: <KEY>                 # the file must contain exactly one `KEY: value` line
    expected: <literal>        # value must equal this (after stripping), or
    pattern: <regex>           # value must re.fullmatch this
    # FAILs if the key is missing, appears more than once, or the value
    # doesn't match -- a structured single answer, so hedging with several
    # candidates (or burying the answer in prose) can't pass by accident.

  type: weeder_audit
    before: <path relative to fixture_dir>   # untouched original skill dir
    after: <path relative to the working copy>  # the agent's result
    min_reduction_pct: <float>    # skill_md_tokens (description + on-trigger body) reduction
    min_preserved_ratio: <float, default 1.0>   # weeder test-function ratio

  type: all
    checks: [<nested verify spec>, ...]   # ALL must pass
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from .types import REPO_ROOT, TaskSpec

WEEDER_CLI = REPO_ROOT / "skills" / "weeder" / "scripts" / "weeder.py"


def _verify_pytest(spec: dict[str, Any], workdir: Path) -> tuple[bool, str]:
    args = spec.get("args", [])
    env = os.environ.copy()
    # If this environment has context-garden installed editable, its .pth
    # entry points at REPO_ROOT/src and would otherwise shadow the
    # materialized copy's own (possibly patched) src/ -- put the copy's
    # src/ first so verification actually exercises what's on disk here.
    local_src = workdir / "src"
    if local_src.is_dir():
        env["PYTHONPATH"] = os.pathsep.join([str(local_src), env.get("PYTHONPATH", "")])
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *args],
        cwd=workdir,
        capture_output=True,
        text=True,
        env=env,
    )
    tail = (proc.stdout + proc.stderr)[-4000:]
    return proc.returncode == 0, tail


def _verify_file_contains(spec: dict[str, Any], workdir: Path) -> tuple[bool, str]:
    path = workdir / spec["path"]
    if not path.exists():
        return False, f"missing file: {spec['path']}"
    text = path.read_text(encoding="utf-8")
    missing = [needle for needle in spec.get("contains", []) if needle not in text]
    if missing:
        return False, f"missing required substring(s): {missing}"
    # `matches`: regexes (re.search), for answers with more than one
    # acceptable phrasing where a single literal substring is too strict.
    unmatched = [pat for pat in spec.get("matches", []) if not re.search(pat, text)]
    if unmatched:
        return False, f"no match for required pattern(s): {unmatched}"
    return True, "ok"


def parse_answer_lines(text: str, key: str) -> list[str]:
    """Every value of a `KEY: value` line in text (key match is exact,
    case-sensitive; surrounding whitespace and backticks are ignored)."""
    line_re = re.compile(rf"^\s*`?{re.escape(key)}\s*:\s*(.*?)`?\s*$")
    values = []
    for line in text.splitlines():
        m = line_re.match(line)
        if m:
            values.append(m.group(1).strip().strip("`").strip())
    return values


def _verify_answer_key(spec: dict[str, Any], workdir: Path) -> tuple[bool, str]:
    key = spec["key"]
    if ("expected" in spec) == ("pattern" in spec):
        raise ValueError("answer_key needs exactly one of `expected` or `pattern`")
    path = workdir / spec["path"]
    if not path.exists():
        return False, f"missing file: {spec['path']}"
    values = parse_answer_lines(path.read_text(encoding="utf-8"), key)
    if not values:
        return False, f"missing answer line `{key}: <value>` in {spec['path']}"
    if len(values) > 1:
        return False, f"{len(values)} `{key}:` lines (need exactly one): {values}"
    value = values[0]
    if "expected" in spec:
        ok = value == str(spec["expected"])
        want = repr(str(spec["expected"]))
    else:
        ok = re.fullmatch(spec["pattern"], value) is not None
        want = f"/{spec['pattern']}/"
    if not ok:
        return False, f"wrong answer: {key}: {value!r} (expected {want})"
    return True, f"{key}: {value}"


def _verify_weeder_audit(spec: dict[str, Any], task: TaskSpec, workdir: Path) -> tuple[bool, str]:
    before_dir = task.fixture_dir / spec["before"]
    after_dir = workdir / spec["after"]
    if not after_dir.is_dir():
        return False, f"missing result skill dir: {spec['after']}"

    def audit(skill_dir: Path) -> dict[str, Any]:
        proc = subprocess.run(
            [sys.executable, str(WEEDER_CLI), "audit", str(skill_dir), "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(proc.stdout)

    before_report = audit(before_dir)
    after_report = audit(after_dir)
    before_tokens = before_report["skill_md_tokens"]
    after_tokens = after_report["skill_md_tokens"]
    reduction_pct = 100.0 * (1 - after_tokens / before_tokens) if before_tokens else 0.0

    func_proc = subprocess.run(
        [
            sys.executable,
            str(WEEDER_CLI),
            "test-function",
            str(before_dir),
            str(after_dir),
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    func_report = json.loads(func_proc.stdout)
    preserved_ratio = func_report["preserved_ratio"]

    min_reduction = spec.get("min_reduction_pct", 0.0)
    min_preserved = spec.get("min_preserved_ratio", 1.0)
    ok = reduction_pct >= min_reduction and preserved_ratio >= min_preserved
    notes = (
        f"skill_md_tokens: {before_tokens} -> {after_tokens} "
        f"({reduction_pct:.1f}% reduction, need >= {min_reduction}%); "
        f"constraint preserved_ratio: {preserved_ratio:.0%} (need >= {min_preserved:.0%})"
    )
    if func_report.get("missing"):
        notes += f"; missing constraints: {func_report['missing']}"
    return ok, notes


def run_verification(task: TaskSpec, workdir: Path) -> tuple[bool, str]:
    return _dispatch(task.verify, task, workdir)


def _dispatch(spec: dict[str, Any], task: TaskSpec, workdir: Path) -> tuple[bool, str]:
    kind = spec["type"]
    if kind == "pytest":
        return _verify_pytest(spec, workdir)
    if kind == "file_contains":
        return _verify_file_contains(spec, workdir)
    if kind == "answer_key":
        return _verify_answer_key(spec, workdir)
    if kind == "weeder_audit":
        return _verify_weeder_audit(spec, task, workdir)
    if kind == "all":
        oks, notes = [], []
        for sub in spec["checks"]:
            ok, note = _dispatch(sub, task, workdir)
            oks.append(ok)
            notes.append(f"[{sub['type']}] {'PASS' if ok else 'FAIL'}: {note}")
        return all(oks), "\n".join(notes)
    raise ValueError(f"unknown verify type: {kind!r}")
