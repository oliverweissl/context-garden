"""Profile detection and per-tool streaming parsers.

A parser subclasses `_Parser(exit_code)`: `feed(lineno, line)` per ANSI-stripped
line, then `finish()` returns `{"events": [...], "numerical_summary": dict|None}`.
Keep state proportional to events, never to lines. Adding a profile: a detection
flag in `ProfileDetector._FLAGS` (+ precedence in `result()`) and an entry in `PARSERS`.
"""

from __future__ import annotations

import collections
import re

ERROR = "error"
WARNING = "warning"

_ARTIFACT_RE = re.compile(
    r"\b(?:wrote|saved|generated|created|writing)\b.*?([./\w\-]+\.\w{1,6})", re.IGNORECASE
)
MAX_ARTIFACTS = 200


# CSI (ESC [ ... final byte) and OSC (ESC ] ... BEL or ESC \\) sequences, plus
# stray two-byte escapes -- emitted by `clang -fcolor-diagnostics`,
# `pytest --color=yes`, ninja, etc. Stripped before parsing only; the stored
# raw output keeps them verbatim.
_ANSI_RE = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|\x1b[@-Z\\-_]"
)


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


_EXPLICIT_MARKER_RE = re.compile(r"^\s*(WARNING|ERROR|FATAL)\b[:\s]", re.IGNORECASE)


def _explicit_marker(i: int, line: str) -> dict | None:
    """Explicit WARNING:/ERROR:/FATAL: prefixed lines, regardless of profile,
    so plain log markers survive a profile parser that ignores them."""
    m = _EXPLICIT_MARKER_RE.match(line)
    if not m:
        return None
    severity = WARNING if m.group(1).upper() == "WARNING" else ERROR
    return {"line": i, "message": line.strip(), "severity": severity}


def scan_explicit_markers(lines) -> list[dict]:
    return [e for e in (_explicit_marker(i, l) for i, l in enumerate(lines, 1)) if e]


class _Parser:
    def __init__(self, exit_code: int = 0):
        self.exit_code = exit_code
        self.events: list[dict] = []

    def feed(self, i: int, line: str) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def finish(self) -> dict:
        return {"events": self.events, "numerical_summary": None}


def _run(cls, lines, exit_code: int) -> dict:
    p = cls(exit_code)
    artifacts = set()
    for i, line in enumerate(lines, start=1):
        p.feed(i, line)
        m = _ARTIFACT_RE.search(line)
        if m:
            artifacts.add(m.group(1))
    out = p.finish()
    out["artifacts"] = sorted(artifacts)
    return out


# ---------------------------------------------------------------- detection

_GCC_DIAG_SEARCH_RE = re.compile(r"\S+:\d+:(?:\d+:)?\s*(?:error|warning):")
_SOLVER_SEARCH_RE = re.compile(r"iter(?:ation)?\D{0,5}\d+.{0,30}?resid", re.IGNORECASE)


class ProfileDetector:
    """Streaming profile detection: feed every line, then `result(command)`."""

    _FLAGS = (
        ("pytest", lambda l, lo: "short test summary info" in lo
         or re.search(r"={3,} failures ={3,}", lo)),
        ("ctest", lambda l, lo: "tests failed out of" in lo or "the following tests failed" in lo),
        ("cmake", lambda l, lo: "cmake error" in lo or "cmake warning" in lo),
        ("slurm", lambda l, lo: "slurmstepd" in lo or re.search(r"\bslurm\b", lo)),
        ("gcc", lambda l, lo: _GCC_DIAG_SEARCH_RE.search(l)),
        ("python_traceback", lambda l, lo: "traceback (most recent call last):" in lo),
        ("numerical_solver", lambda l, lo: _SOLVER_SEARCH_RE.search(l)),
    )

    def __init__(self):
        self.seen: set = set()

    def feed(self, line: str) -> None:
        if len(self.seen) == len(self._FLAGS):
            return
        lower = line.lower()
        for name, test in self._FLAGS:
            if name not in self.seen and test(line, lower):
                self.seen.add(name)

    def result(self, command: str) -> str:
        cmd = (command or "").lower()
        s = self.seen
        if "pytest" in cmd or "pytest" in s:
            return "pytest"
        if "ctest" in cmd or "ctest" in s:
            return "ctest"
        if "cmake" in s or ("cmake" in cmd and "--build" not in cmd):
            return "cmake"
        if "srun" in cmd or "sbatch" in cmd or "slurm" in s:
            return "slurm"
        if "gcc" in s or any(
            f" {c} " in f" {cmd} " or cmd.startswith(c)
            for c in ("gcc", "g++", "clang", "clang++", "cc", "c++")
        ):
            return "gcc"
        if "python_traceback" in s:
            return "python_traceback"
        if "numerical_solver" in s:
            return "numerical_solver"
        return "generic"


def detect_profile(command: str, text: str) -> str:
    d = ProfileDetector()
    for line in text.splitlines():
        d.feed(line)
    return d.result(command)


# ---------------------------------------------------------------- generic

_ERROR_KEYWORDS = re.compile(r"\b(error|exception|fatal|failed|failure)\b", re.IGNORECASE)
_WARNING_KEYWORDS = re.compile(r"\bwarning\b", re.IGNORECASE)
# "0 failed", "0 errors", "no failures" -- counts of zero are not failures
_ZERO_COUNT_RE = re.compile(
    r"\b(?:0|no|zero)\s+(?:errors?|exceptions?|failed|failures?|fatal)\b", re.IGNORECASE
)


class GenericParser(_Parser):
    def feed(self, i, line):
        # a bare keyword is a weak signal: on exit 0 it never escalates to an error
        if _ERROR_KEYWORDS.search(_ZERO_COUNT_RE.sub("", line)):
            sev = ERROR if self.exit_code != 0 else WARNING
            self.events.append({"line": i, "message": line.strip(), "severity": sev})
        elif _WARNING_KEYWORDS.search(line):
            self.events.append({"line": i, "message": line.strip(), "severity": WARNING})


# ---------------------------------------------------------------- pytest

_PYTEST_SUMMARY_RE = re.compile(r"^(FAILED|ERROR)\s+(\S+)\s*(?:-\s*(.*))?$")
_PYTEST_E_LINE_RE = re.compile(r"^E\s+(\w[\w.]*(?:Error|Exception|Warning))?:?\s*(.*)$")
_PYTEST_FAILURE_HEADER_RE = re.compile(r"^_{3,}\s+(.+?)\s+_{3,}$")
_PYTEST_COLLECT_HEADER_RE = re.compile(r"^ERROR collecting\s+(.+)$")
_PYTEST_FINAL_RE = re.compile(
    r"^=*\s*\d+ (?:passed|failed|errors?|skipped|xfailed|xpassed|deselected|warnings?)\b"
    r".*\bin [\d.]+s\b.*?=*$"
)
_PYTEST_FALLBACK_CAP = 10000


def _pytest_reason_usable(reason: str | None) -> bool:
    # pytest truncates the summary line to terminal width ("T..." when not a
    # tty); such a reason is worse than the FAILURES-section message.
    return bool(reason) and not reason.rstrip().endswith("...")


class PytestParser(_Parser):
    """One event per failing test (`test` set), from `short test summary info`.

    Messages prefer the summary reason; truncated/missing reasons fall back to
    the FAILURES/ERRORS section's `E` lines: the first `E` line naming an
    exception type wins, else the first `E` line (later ones are usually diff
    detail like "Use -v to get more diff")."""

    def __init__(self, exit_code=0):
        super().__init__(exit_code)
        self.typed: dict = {}
        self.first: dict = {}
        self.current = None
        self.in_summary = False
        self.pending: list = []  # (line, kind, test, reason) resolved in finish()
        self.fallback: list = []
        self.final_line = None
        self.last_passed = None
        self.last_failed = None

    def feed(self, i, line):
        stripped = line.strip()
        if _PYTEST_FINAL_RE.search(stripped):
            self.final_line = stripped
        m = re.search(r"(\d+) passed", line)
        if m:
            self.last_passed = m.group(1)
        m = re.search(r"(\d+) failed", line)
        if m:
            self.last_failed = m.group(1)

        header = _PYTEST_FAILURE_HEADER_RE.match(stripped)
        if header:
            self.current = header.group(1)
            collect = _PYTEST_COLLECT_HEADER_RE.match(self.current)
            if collect:
                self.current = collect.group(1)
        else:
            m = _PYTEST_E_LINE_RE.match(stripped)
            if m:
                kind, msg = m.groups()
                if self.current is not None:
                    if kind and self.current not in self.typed:
                        self.typed[self.current] = f"{kind}: {msg}"
                    elif msg and self.current not in self.first:
                        self.first[self.current] = msg
                if kind and len(self.fallback) < _PYTEST_FALLBACK_CAP:
                    self.fallback.append(
                        {"line": i, "message": f"{kind}: {msg}", "severity": ERROR}
                    )

        if "short test summary info" in stripped.lower():
            self.in_summary = True
            return
        if self.in_summary:
            if stripped.startswith("=") or stripped == "":
                if stripped.startswith("="):
                    self.in_summary = False
                return
            m = _PYTEST_SUMMARY_RE.match(stripped)
            if m:
                self.pending.append((i,) + m.groups())

    def finish(self):
        full = {**self.first, **self.typed}
        tests_failed = []
        for i, kind, test, reason in self.pending:
            tests_failed.append(test)
            # test names in the FAILURES header omit the file/module prefix
            # that "short test summary info" includes -- match on the
            # trailing segment (e.g. "test_x[0]" in both).
            short_name = test.rsplit("::", 1)[-1]
            if _pytest_reason_usable(reason):
                message = reason.strip()
            else:
                message = full.get(test) or full.get(short_name) or f"{kind} {test}"
            self.events.append({"line": i, "message": message, "severity": ERROR, "test": test})
        if not self.events:
            self.events = self.fallback

        # counts come from pytest's final summary line ("=== 3 failed, 5 passed
        # in 1.2s ===", or "3 failed, 5 passed in 1.2s" under -q), not from the
        # first "N passed" that happens to appear in captured test output.
        if self.final_line is not None:
            pm = re.search(r"(\d+) passed", self.final_line)
            fm = re.search(r"(\d+) failed", self.final_line)
            passed = pm.group(1) if pm else None
            failed = fm.group(1) if fm else None
        else:
            passed, failed = self.last_passed, self.last_failed
        ns = None
        if passed is not None or failed is not None:
            ns = {
                "tests_passed": int(passed) if passed is not None else 0,
                "tests_failed": int(failed) if failed is not None else len(set(tests_failed)),
            }
        return {"events": self.events, "numerical_summary": ns}


# ---------------------------------------------------------------- ctest

_CTEST_FAILED_RE = re.compile(r"^\s*\d+\s*-\s*(\S+)\s*\((\w+)\)")


class CtestParser(_Parser):
    def __init__(self, exit_code=0):
        super().__init__(exit_code)
        self.in_list = False

    def feed(self, i, line):
        if "the following tests failed" in line.lower():
            self.in_list = True
            return
        if self.in_list:
            m = _CTEST_FAILED_RE.match(line)
            if m:
                name, status = m.groups()
                self.events.append(
                    {"line": i, "message": f"{name} ({status})", "severity": ERROR, "test": name}
                )
            elif line.strip() == "":
                self.in_list = False


# ---------------------------------------------------------------- gcc/clang

_GCC_RE = re.compile(
    r"^(?P<file>[^:\s][^:]*):(?P<line>\d+):(?:(?P<col>\d+):)?\s*(?P<sev>error|warning|note):\s*(?P<msg>.*)$"
)
# gcc instantiation backtrace, printed *before* the error it explains:
#   src/main.cpp:14:14:   required from here
#   /usr/include/c++/11/bits/stl_algo.h:1866:25:   required from 'void std::...'
_GCC_REQUIRED_RE = re.compile(
    r"^(?P<file>[^:\s][^:]*):(?P<line>\d+):(?:(?P<col>\d+):)?\s+"
    r"(?:required from|required by|instantiated from)\b"
)
#   In file included from src/main.cpp:3:     /     from src/app.hpp:7,
_INCLUDED_RE = re.compile(
    r"^(?:In file included from|from)\s+(?P<file>[^:\s][^:]*):(?P<line>\d+)(?::(?P<col>\d+))?[:,]?$"
)
_INSTANTIATION_NOTE_RE = re.compile(
    r"requested here|required from|in instantiation of|instantiated from", re.IGNORECASE
)
_CONTEXT_CAP = 32


def _loc(m) -> dict:
    loc = f"{m.group('file')}:{m.group('line')}"
    if m.group("col"):
        loc += f":{m.group('col')}"
    return {"file": m.group("file"), "loc": loc}


class GccParser(_Parser):
    """gcc/clang diagnostics; `note:` lines are not events, but locations from
    instantiation backtraces (gcc's `required from here` before the error,
    clang's `note: ... requested here` after it) and include chains are kept
    as `context` on the error they explain, so the summary can point at the
    user-code call site of template spam from a system header."""

    def __init__(self, exit_code=0):
        super().__init__(exit_code)
        self.pending: list = []  # context seen before the next diagnostic
        self.last = None  # last error/warning event (clang notes follow it)

    def _add_ctx(self, ev, kind, loc):
        ctx = ev.setdefault("context", [])
        if len(ctx) < _CONTEXT_CAP:
            ctx.append(dict(loc, kind=kind))

    def feed(self, i, line):
        s = line.strip()
        m = _GCC_RE.match(s)
        if m:
            sev = m.group("sev")
            if sev == "note":
                if self.last is not None and _INSTANTIATION_NOTE_RE.search(m.group("msg")):
                    self._add_ctx(self.last, "instantiation", _loc(m))
                return
            ev = {
                "line": i,
                "message": f"{m.group('file')}:{m.group('line')}: {m.group('msg')}",
                "severity": ERROR if sev == "error" else WARNING,
                "file": m.group("file"),
                "text": m.group("msg"),
            }
            for kind, loc in self.pending:
                self._add_ctx(ev, kind, loc)
            self.pending = []
            self.events.append(ev)
            self.last = ev
            return
        m = _GCC_REQUIRED_RE.match(s)
        if m:
            if len(self.pending) < _CONTEXT_CAP:
                self.pending.append(("instantiation", _loc(m)))
            return
        m = _INCLUDED_RE.match(s)
        if m and len(self.pending) < _CONTEXT_CAP:
            self.pending.append(("include", _loc(m)))


_LINKER_RE = re.compile(
    r"undefined reference to|ld: symbol\(s\) not found|ld returned \d+ exit status"
    r"|Undefined symbols for architecture"
    r"|linker command failed with exit code"
)

# Profiles whose output may interleave compiler/linker diagnostics from a
# build driver (cmake --build, make, ninja, ...): gcc/clang + linker
# parsing always runs on top of them as a supplement.
BUILD_PROFILES = ("cmake", "gcc", "generic")


class BuildScanner(_Parser):
    """gcc/clang diagnostics plus linker failures, regardless of profile."""

    def __init__(self, exit_code=0):
        super().__init__(exit_code)
        self.gcc = GccParser(0)
        self.linker: list = []

    def feed(self, i, line):
        self.gcc.feed(i, line)
        if _LINKER_RE.search(line):
            self.linker.append({"line": i, "message": line.strip(), "severity": ERROR})

    def finish(self):
        return {"events": self.gcc.events + self.linker, "numerical_summary": None}


def scan_build_diagnostics(lines) -> list[dict]:
    return _run(BuildScanner, lines, 0)["events"]


# ---------------------------------------------------------------- cmake

_CMAKE_RE = re.compile(r"^CMake (Error|Warning)(?: \(dev\))? at ([^:]+):(\d+)")


class CmakeParser(_Parser):
    """Each `CMake Error|Warning at file:line` header plus its indented
    continuation lines becomes one event."""

    def __init__(self, exit_code=0):
        super().__init__(exit_code)
        self.block = None  # (line, kind, file, lnum, header, parts)

    def _close(self):
        if self.block is None:
            return
        i, kind, file, lnum, header, parts = self.block
        message = " ".join(parts) if parts else header
        self.events.append(
            {
                "line": i,
                "message": f"{file}:{lnum}: {message}",
                "severity": ERROR if kind == "Error" else WARNING,
            }
        )
        self.block = None

    def feed(self, i, line):
        if self.block is not None:
            if line.startswith("  ") or line.strip() == "":
                if line.strip() and len(self.block[5]) < 50:
                    self.block[5].append(line.strip())
                return
            self._close()
        m = _CMAKE_RE.match(line.strip())
        if m:
            kind, file, lnum = m.groups()
            self.block = (i, kind, file, lnum, line.strip(), [])

    def finish(self):
        self._close()
        return super().finish()


# ---------------------------------------------------------------- python traceback


class PythonTracebackParser(_Parser):
    """Each traceback's final (exception) line becomes one event."""

    def __init__(self, exit_code=0):
        super().__init__(exit_code)
        self.in_tb = False

    def feed(self, i, line):
        if self.in_tb:
            if line.startswith((" ", "\t")) or line.strip() == "":
                return
            self.events.append({"line": i, "message": line.strip(), "severity": ERROR})
            self.in_tb = False
            return
        if line.strip() == "Traceback (most recent call last):":
            self.in_tb = True


# ---------------------------------------------------------------- slurm

_SLURM_PATTERNS = [
    (re.compile(r"oom-kill", re.IGNORECASE), "out_of_memory"),
    (re.compile(r"due to time limit", re.IGNORECASE), "time_limit_exceeded"),
    (re.compile(r"exceeded (?:step|job) memory limit", re.IGNORECASE), "memory_limit_exceeded"),
    (re.compile(r"\bcancelled\b", re.IGNORECASE), "cancelled"),
    (re.compile(r"slurmstepd: error", re.IGNORECASE), "step_error"),
]
_SLURM_MEM_RE = re.compile(r"Max(?:RSS|VMSize)[=: ]+([\d.]+\s*\w*)")


class SlurmParser(_Parser):
    def __init__(self, exit_code=0):
        super().__init__(exit_code)
        self.termination_reason = None
        self.peak_memory = None

    def feed(self, i, line):
        for pat, reason in _SLURM_PATTERNS:
            if pat.search(line):
                self.events.append({"line": i, "message": line.strip(), "severity": ERROR})
                self.termination_reason = self.termination_reason or reason
                break  # one event per line: first (highest-priority) pattern wins
        m = _SLURM_MEM_RE.search(line)
        if m:
            self.peak_memory = m.group(1).strip()

    def finish(self):
        ns = {
            "termination_reason": self.termination_reason
            or ("clean_exit" if self.exit_code == 0 else "unknown"),
            "peak_memory": self.peak_memory,
        }
        return {"events": self.events, "numerical_summary": ns}


# ---------------------------------------------------------------- numerical solver

_ITER_RE = re.compile(
    r"iter(?:ation)?\D{0,5}(\d+).{0,30}?resid\w*\D{0,5}([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?|nan|-?inf)",
    re.IGNORECASE,
)
# standalone numeric nan/inf tokens only: not "inf-norm", "info", "x-inf"
_NANINF_RE = re.compile(r"(?<![\w.-])[-+]?(?:nan|inf(?:inity)?)(?![\w-])", re.IGNORECASE)
# trend is computed over the last _RESID_TAIL residuals only
_RESID_TAIL = 4096


def _residual_trend(values: list) -> str:
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


class NumericalSolverParser(_Parser):
    def __init__(self, exit_code=0):
        super().__init__(exit_code)
        self.iterations = 0
        self.initial = None
        self.valid = collections.deque(maxlen=_RESID_TAIL)
        self.nan = False

    def feed(self, i, line):
        m = _ITER_RE.search(line)
        if m:
            self.iterations += 1
            _, val = m.groups()
            if val.lower() in ("nan", "inf", "-inf"):
                self.nan = True
                self.events.append({"line": i, "message": line.strip(), "severity": ERROR})
            else:
                v = float(val)
                if self.initial is None:
                    self.initial = v
                self.valid.append(v)
        elif _NANINF_RE.search(line):
            self.nan = True
            self.events.append({"line": i, "message": line.strip(), "severity": WARNING})

    def finish(self):
        valid = list(self.valid)
        ns = {
            "iterations_observed": self.iterations,
            "initial_residual": self.initial,
            "final_residual": valid[-1] if valid else None,
            "trend": _residual_trend(valid),
            "nan_or_inf_detected": self.nan,
        }
        return {"events": self.events, "numerical_summary": ns}


PARSERS = {
    "generic": GenericParser,
    "pytest": PytestParser,
    "ctest": CtestParser,
    "gcc": GccParser,
    "cmake": CmakeParser,
    "python_traceback": PythonTracebackParser,
    "slurm": SlurmParser,
    "numerical_solver": NumericalSolverParser,
}


# ---------------------------------------------------------------- one-pass driver


def analyse(lines, profile: str, exit_code: int, head_lines: int, tail_lines: int) -> dict:
    """Run the profile parser (+ build-diagnostic and explicit-marker
    supplements, artifact scan) over `lines` (an iterator of ANSI-stripped
    lines) in a single pass. Only the first `head_lines` and last
    `tail_lines` lines are retained verbatim."""
    parser = PARSERS.get(profile, GenericParser)(exit_code)
    build = BuildScanner(exit_code) if profile in BUILD_PROFILES else None
    explicit: list = []
    artifacts: set = set()
    head: list = []
    tail = collections.deque(maxlen=tail_lines)
    n = 0
    for n, line in enumerate(lines, start=1):
        parser.feed(n, line)
        if build is not None:
            build.feed(n, line)
        e = _explicit_marker(n, line)
        if e:
            explicit.append(e)
        m = _ARTIFACT_RE.search(line)
        if m and len(artifacts) < MAX_ARTIFACTS:
            artifacts.add(m.group(1))
        if n <= head_lines:
            head.append(line)
        tail.append(line)

    parsed = parser.finish()
    events = parsed["events"]
    if build is not None:
        # build drivers (cmake --build, make, ninja) interleave compiler and
        # linker output: those diagnostics take precedence on the same line
        build_events = build.finish()["events"]
        build_lines = {e["line"] for e in build_events}
        events = build_events + [e for e in events if e["line"] not in build_lines]
    seen = {e["line"] for e in events}
    events += [e for e in explicit if e["line"] not in seen]
    events.sort(key=lambda e: e["line"])
    return {
        "events": events,
        "numerical_summary": parsed.get("numerical_summary"),
        "artifacts": sorted(artifacts),
        "line_count": n,
        "head": head,
        "tail": list(tail),
    }
