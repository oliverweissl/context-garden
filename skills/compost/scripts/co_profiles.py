"""Profile detection and per-tool deterministic parsers.

Every parser takes (lines, exit_code) and returns:
    {
        "events": [{"line": int, "message": str, "severity": "error"|"warning", "test": str?}, ...],
        "numerical_summary": dict | None,
        "artifacts": [str, ...],
    }
No LLM calls anywhere in this module -- everything is regex/state-machine based.
"""
from __future__ import annotations

import re

ERROR = "error"
WARNING = "warning"

_ARTIFACT_RE = re.compile(
    r"\b(?:wrote|saved|generated|created|writing)\b.*?([./\w\-]+\.\w{1,6})", re.IGNORECASE
)


def _scan_artifacts(lines: list[str]) -> list[str]:
    found = []
    for line in lines:
        m = _ARTIFACT_RE.search(line)
        if m:
            found.append(m.group(1))
    return sorted(set(found))


_EXPLICIT_MARKER_RE = re.compile(r"^\s*(WARNING|ERROR|FATAL)\b[:\s]", re.IGNORECASE)


def scan_explicit_markers(lines: list[str]) -> list[dict]:
    """Catch explicit WARNING:/ERROR:/FATAL: prefixed lines regardless of profile.

    Profile parsers target each tool's specific structure (tracebacks, diagnostic
    format, summary tables); this is a cheap supplement so a plain log line like
    'WARNING: slow convergence detected' is never silently dropped just because
    the active profile parser wasn't looking for it.
    """
    events = []
    for i, line in enumerate(lines, start=1):
        m = _EXPLICIT_MARKER_RE.match(line)
        if m:
            severity = WARNING if m.group(1).upper() == "WARNING" else ERROR
            events.append({"line": i, "message": line.strip(), "severity": severity})
    return events


# ---------------------------------------------------------------- detection

def detect_profile(command: str, text: str) -> str:
    cmd = (command or "").lower()
    lower = text.lower()
    if "pytest" in cmd or "short test summary info" in lower or re.search(r"={3,} failures ={3,}", lower):
        return "pytest"
    if "ctest" in cmd or "tests failed out of" in lower or "the following tests failed" in lower:
        return "ctest"
    if "cmake" in cmd or "cmake error" in lower or "cmake warning" in lower:
        return "cmake"
    if "srun" in cmd or "sbatch" in cmd or "slurmstepd" in lower or re.search(r"\bslurm\b", lower):
        return "slurm"
    if any(f" {c} " in f" {cmd} " or cmd.startswith(c) for c in ("gcc", "g++", "clang", "clang++", "cc", "c++")) or re.search(
        r"\S+:\d+:(?:\d+:)?\s*(?:error|warning):", text
    ):
        return "gcc"
    if "traceback (most recent call last):" in lower:
        return "python_traceback"
    if re.search(r"iter(?:ation)?\D{0,5}\d+.{0,30}?resid", text, re.IGNORECASE):
        return "numerical_solver"
    return "generic"


# ---------------------------------------------------------------- generic

_ERROR_KEYWORDS = re.compile(r"\b(error|exception|fatal|failed|failure)\b", re.IGNORECASE)
_WARNING_KEYWORDS = re.compile(r"\bwarning\b", re.IGNORECASE)


def parse_generic(lines: list[str], exit_code: int) -> dict:
    events = []
    for i, line in enumerate(lines, start=1):
        if _ERROR_KEYWORDS.search(line):
            events.append({"line": i, "message": line.strip(), "severity": ERROR})
        elif _WARNING_KEYWORDS.search(line):
            events.append({"line": i, "message": line.strip(), "severity": WARNING})
    return {"events": events, "numerical_summary": None, "artifacts": _scan_artifacts(lines)}


# ---------------------------------------------------------------- pytest

_PYTEST_SUMMARY_RE = re.compile(r"^(FAILED|ERROR)\s+(\S+)\s*(?:-\s*(.*))?$")
_PYTEST_E_LINE_RE = re.compile(r"^E\s+(\w[\w.]*(?:Error|Exception|Warning))?:?\s*(.*)$")


def parse_pytest(lines: list[str], exit_code: int) -> dict:
    events = []
    tests_failed = []
    in_summary = False
    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        if "short test summary info" in stripped.lower():
            in_summary = True
            continue
        if in_summary:
            if stripped.startswith("=") or stripped == "":
                if stripped.startswith("=") and "short test summary" not in stripped.lower():
                    in_summary = False
                continue
            m = _PYTEST_SUMMARY_RE.match(stripped)
            if m:
                kind, test, reason = m.groups()
                tests_failed.append(test)
                message = reason.strip() if reason else f"{kind} {test}"
                events.append({"line": i, "message": message, "severity": ERROR, "test": test})

    if not events:
        # fall back to scanning inline "E   ExceptionType: message" lines
        for i, line in enumerate(lines, start=1):
            m = _PYTEST_E_LINE_RE.match(line.strip())
            if m and m.group(1):
                events.append({"line": i, "message": f"{m.group(1)}: {m.group(2)}", "severity": ERROR})

    joined = "\n".join(lines)
    passed_m = re.search(r"(\d+) passed", joined)
    failed_m = re.search(r"(\d+) failed", joined)
    numerical_summary = None
    if passed_m or failed_m:
        numerical_summary = {
            "tests_passed": int(passed_m.group(1)) if passed_m else 0,
            "tests_failed": int(failed_m.group(1)) if failed_m else len(set(tests_failed)),
        }
    return {
        "events": events,
        "numerical_summary": numerical_summary,
        "artifacts": _scan_artifacts(lines),
    }


# ---------------------------------------------------------------- ctest

_CTEST_FAILED_RE = re.compile(r"^\s*\d+\s*-\s*(\S+)\s*\((\w+)\)")


def parse_ctest(lines: list[str], exit_code: int) -> dict:
    events = []
    in_list = False
    for i, line in enumerate(lines, start=1):
        if "the following tests failed" in line.lower():
            in_list = True
            continue
        if in_list:
            m = _CTEST_FAILED_RE.match(line)
            if m:
                name, status = m.groups()
                events.append({"line": i, "message": f"{name} ({status})", "severity": ERROR, "test": name})
            elif line.strip() == "":
                in_list = False
    return {"events": events, "numerical_summary": None, "artifacts": _scan_artifacts(lines)}


# ---------------------------------------------------------------- gcc/clang

_GCC_RE = re.compile(
    r"^(?P<file>[^:\s][^:]*):(?P<line>\d+):(?:(?P<col>\d+):)?\s*(?P<sev>error|warning|note):\s*(?P<msg>.*)$"
)


def parse_gcc(lines: list[str], exit_code: int) -> dict:
    events = []
    for i, line in enumerate(lines, start=1):
        m = _GCC_RE.match(line.strip())
        if not m:
            continue
        sev = m.group("sev")
        if sev == "note":
            continue
        severity = ERROR if sev == "error" else WARNING
        events.append(
            {
                "line": i,
                "message": f"{m.group('file')}:{m.group('line')}: {m.group('msg')}",
                "severity": severity,
            }
        )
    return {"events": events, "numerical_summary": None, "artifacts": _scan_artifacts(lines)}


# ---------------------------------------------------------------- cmake

_CMAKE_RE = re.compile(r"^CMake (Error|Warning)(?: \(dev\))? at ([^:]+):(\d+)")


def parse_cmake(lines: list[str], exit_code: int) -> dict:
    events = []
    i, n = 0, len(lines)
    while i < n:
        m = _CMAKE_RE.match(lines[i].strip())
        if m:
            kind, file, lnum = m.groups()
            msg_parts = []
            j = i + 1
            while j < n and (lines[j].startswith("  ") or lines[j].strip() == ""):
                if lines[j].strip():
                    msg_parts.append(lines[j].strip())
                j += 1
            message = " ".join(msg_parts) if msg_parts else lines[i].strip()
            events.append(
                {
                    "line": i + 1,
                    "message": f"{file}:{lnum}: {message}",
                    "severity": ERROR if kind == "Error" else WARNING,
                }
            )
            i = j
        else:
            i += 1
    return {"events": events, "numerical_summary": None, "artifacts": _scan_artifacts(lines)}


# ---------------------------------------------------------------- python traceback

def parse_python_traceback(lines: list[str], exit_code: int) -> dict:
    events = []
    i, n = 0, len(lines)
    while i < n:
        if lines[i].strip() == "Traceback (most recent call last):":
            j = i + 1
            while j < n and (lines[j].startswith((" ", "\t")) or lines[j].strip() == ""):
                j += 1
            if j < n:
                events.append({"line": j + 1, "message": lines[j].strip(), "severity": ERROR})
            i = j + 1
        else:
            i += 1
    return {"events": events, "numerical_summary": None, "artifacts": _scan_artifacts(lines)}


# ---------------------------------------------------------------- slurm

_SLURM_PATTERNS = [
    (re.compile(r"oom-kill", re.IGNORECASE), "out_of_memory"),
    (re.compile(r"due to time limit", re.IGNORECASE), "time_limit_exceeded"),
    (re.compile(r"exceeded (?:step|job) memory limit", re.IGNORECASE), "memory_limit_exceeded"),
    (re.compile(r"\bcancelled\b", re.IGNORECASE), "cancelled"),
    (re.compile(r"slurmstepd: error", re.IGNORECASE), "step_error"),
]
_SLURM_MEM_RE = re.compile(r"Max(?:RSS|VMSize)[=: ]+([\d.]+\s*\w*)")


def parse_slurm(lines: list[str], exit_code: int) -> dict:
    events = []
    termination_reason = None
    peak_memory = None
    for i, line in enumerate(lines, start=1):
        for pat, reason in _SLURM_PATTERNS:
            if pat.search(line):
                events.append({"line": i, "message": line.strip(), "severity": ERROR})
                termination_reason = termination_reason or reason
                break  # one event per line: first (highest-priority) pattern wins
        m = _SLURM_MEM_RE.search(line)
        if m:
            peak_memory = m.group(1).strip()
    numerical_summary = {
        "termination_reason": termination_reason or ("clean_exit" if exit_code == 0 else "unknown"),
        "peak_memory": peak_memory,
    }
    return {"events": events, "numerical_summary": numerical_summary, "artifacts": _scan_artifacts(lines)}


# ---------------------------------------------------------------- numerical solver

_ITER_RE = re.compile(
    r"iter(?:ation)?\D{0,5}(\d+).{0,30}?resid\w*\D{0,5}([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?|nan|-?inf)",
    re.IGNORECASE,
)
_NANINF_RE = re.compile(r"\b(nan|-?inf)\b", re.IGNORECASE)


def _residual_trend(values: list[float]) -> str:
    if len(values) < 2:
        return "insufficient_data"
    diffs = [values[i + 1] - values[i] for i in range(len(values) - 1)]
    decreasing = sum(1 for d in diffs if d < 0)
    increasing = sum(1 for d in diffs if d > 0)
    sign_changes = sum(1 for i in range(len(diffs) - 1) if diffs[i] * diffs[i + 1] < 0)
    if len(diffs) >= 3 and sign_changes >= len(diffs) * 0.4:
        return "oscillatory"
    if decreasing >= increasing * 2:
        return "converging"
    if increasing >= decreasing * 2:
        return "diverging"
    return "mixed"


def parse_numerical_solver(lines: list[str], exit_code: int) -> dict:
    events = []
    residuals = []
    nan_detected = False
    for i, line in enumerate(lines, start=1):
        m = _ITER_RE.search(line)
        if m:
            it, val = m.groups()
            if val.lower() in ("nan", "inf", "-inf"):
                nan_detected = True
                events.append({"line": i, "message": line.strip(), "severity": ERROR})
                residuals.append((int(it), None))
            else:
                residuals.append((int(it), float(val)))
        elif _NANINF_RE.search(line):
            nan_detected = True
            events.append({"line": i, "message": line.strip(), "severity": WARNING})

    valid = [r for _, r in residuals if r is not None]
    numerical_summary = {
        "iterations_observed": len(residuals),
        "initial_residual": valid[0] if valid else None,
        "final_residual": valid[-1] if valid else None,
        "trend": _residual_trend(valid),
        "nan_or_inf_detected": nan_detected,
    }
    return {"events": events, "numerical_summary": numerical_summary, "artifacts": _scan_artifacts(lines)}


PARSERS = {
    "generic": parse_generic,
    "pytest": parse_pytest,
    "ctest": parse_ctest,
    "gcc": parse_gcc,
    "cmake": parse_cmake,
    "python_traceback": parse_python_traceback,
    "slurm": parse_slurm,
    "numerical_solver": parse_numerical_solver,
}
