#!/usr/bin/env python3
"""compost: capture, cluster and compact-summarize large tool output.

See ../SKILL.md for the agent-facing workflow and ../references/schema.md
for the full output schema.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from co_cluster import (
    cluster_events,
    compact_ranges,
    event_signature,
    is_user_path,
    pick_root_error,
    user_location,
)
from co_profiles import PARSERS, ProfileDetector, analyse, strip_ansi
from co_store import Store, find_repo_root, find_store_root

# Absolute path of the wrapper, so printed retrieval handles are runnable
# as-is (`compost` itself is not expected to be on PATH).
BIN = str(Path(__file__).resolve().parent.parent / "bin" / "compost")
_handle_prefix = BIN

MAX_GET_LINES = 400
ROOT_EVENT_CAP = 1
REPEATED_EVENT_CAP = 10
WARNING_CAP = 5
FAILING_TESTS_SHOWN = 10
DIFF_TESTS_SHOWN = 20
RAW_TAIL_LINES = 40
# at or below this many lines, the (ANSI-stripped) output itself is shown
# instead of a structured summary that would be larger than the input
SMALL_OUTPUT_LINES = 30
TIMEOUT_EXIT = 124
# per-group caps for what meta.json keeps (full detail stays in raw.txt)
META_LINES_CAP = 50
META_TESTS_CAP = 200


# ---------------------------------------------------------------- summary building


def _derive_status(exit_code: int, error_groups: list[dict], parsed: dict) -> str:
    # nan/inf only fails the run through an error event (a nan residual) or a
    # nonzero exit; a stray standalone "nan" token is a warning, not a FAIL
    if exit_code != 0 or error_groups:
        return "fail"
    return "pass"


def _public_event(e: dict) -> dict:
    out = {
        "event_id": e["index"],
        "message": e["message"],
        "count": e["count"],
        "lines": e.get("line_ranges") or compact_ranges(e["lines"]),
    }
    if e.get("user_location"):
        out["user_location"] = e["user_location"]
    if e.get("tests"):
        out["affected_tests_count"] = e.get("tests_count", len(e["tests"]))
        out["affected_tests"] = e["tests"][:20]
    return out


def compute_diff(
    prev_meta: dict,
    exit_code: int,
    numerical_summary: dict | None,
    curr_sig_map: dict,
    curr_failing_tests: list | None = None,
) -> dict:
    prev_sigs = prev_meta.get("group_signatures", {})
    new_events, resolved_events, count_changes = [], [], []
    for sig, info in curr_sig_map.items():
        if sig not in prev_sigs:
            new_events.append(info)
        elif prev_sigs[sig]["count"] != info["count"]:
            count_changes.append(
                {
                    "message": info["message"],
                    "before": prev_sigs[sig]["count"],
                    "after": info["count"],
                }
            )
    for sig, info in prev_sigs.items():
        if sig not in curr_sig_map:
            resolved_events.append(info)
    diff = {
        "previous_run": prev_meta["raw_output_id"],
        "exit_code_before": prev_meta["exit_code"],
        "exit_code_after": exit_code,
        "new_events": new_events,
        "resolved_events": resolved_events,
        "count_changes": count_changes,
    }
    prev_failing = set(prev_meta.get("failing_test_ids", []))
    curr_failing = set(curr_failing_tests or [])
    newly_failing = sorted(curr_failing - prev_failing)
    newly_passing = sorted(prev_failing - curr_failing)
    diff["newly_failing_tests_count"] = len(newly_failing)
    diff["newly_failing_tests"] = newly_failing[:DIFF_TESTS_SHOWN]
    diff["newly_passing_tests_count"] = len(newly_passing)
    diff["newly_passing_tests"] = newly_passing[:DIFF_TESTS_SHOWN]
    if prev_meta.get("numerical_summary") and numerical_summary:
        diff["numerical_summary_before"] = prev_meta["numerical_summary"]
        diff["numerical_summary_after"] = numerical_summary
    return diff


def _clean_lines(store: Store, run_id: str):
    # parse an ANSI-stripped view; raw.txt keeps the verbatim bytes
    return (strip_ansi(l) for l in store.iter_raw(run_id))


def process_and_store(
    store: Store,
    run_id: str,
    command_str: str,
    exit_code: int,
    duration: float,
    profile_override: str | None,
    timed_out: float | None = None,
    signal_info: dict | None = None,
) -> dict:
    """Parse the already-stored raw.txt of `run_id` line by line (memory
    stays flat: nothing but events, the head and a tail ring buffer is held)
    and write the final meta.json."""
    if profile_override:
        profile_name = profile_override
    else:
        detector = ProfileDetector()
        for line in _clean_lines(store, run_id):
            detector.feed(line)
        profile_name = detector.result(command_str)
    parsed = analyse(
        _clean_lines(store, run_id),
        profile_name,
        exit_code,
        head_lines=SMALL_OUTPUT_LINES + 1,
        tail_lines=RAW_TAIL_LINES,
    )
    line_count = parsed["line_count"]
    repo_root = find_repo_root()

    groups = cluster_events(parsed["events"])
    # root error: first error in output order located in user code (see
    # co_cluster.pick_root_error); frequency only orders the remaining groups
    root_ev = pick_root_error([e for e in parsed["events"] if e["severity"] == "error"], repo_root)
    root_group = None
    if root_ev is not None:
        root_sig = event_signature(root_ev)
        root_group = next(g for g in groups if g["signature"] == root_sig)
    rest = sorted(
        (g for g in groups if g is not root_group),
        key=lambda g: (-g["count"], g["representative"]["line"]),
    )
    groups_sorted = ([root_group] if root_group else []) + rest

    events_index = []
    for idx, g in enumerate(groups_sorted, start=1):
        rep = g["representative"]
        tests = sorted(g["tests"])
        entry = {
            "index": idx,
            "message": rep["message"][:300],
            "severity": rep["severity"],
            "count": g["count"],
            "lines": g["lines"][:META_LINES_CAP],
            "line_ranges": compact_ranges(g["lines"]),
            "signature": g["signature"],
            "tests": tests[:META_TESTS_CAP],
            "tests_count": len(tests),
        }
        # template spam from a system header: show where user code triggered it
        if rep.get("file") and not is_user_path(rep["file"], repo_root):
            loc = user_location(root_ev, repo_root) if g is root_group else None
            loc = loc or user_location(rep, repo_root)
            if loc:
                entry["user_location"] = loc
        events_index.append(entry)
        g["tests"] = tests  # full list, for failing_tests below

    error_groups = [g for g in groups_sorted if g["representative"]["severity"] == "error"]
    error_events = [e for e in events_index if e["severity"] == "error"]
    warning_events = [e for e in events_index if e["severity"] == "warning"]

    status = _derive_status(exit_code, error_groups, parsed)
    note = None
    if timed_out is not None:
        status = "timeout"
        note = f"killed after --timeout {timed_out:g}s (process group SIGKILL)"
    elif signal_info:
        status = signal_info["status"]
        note = signal_info["note"]
    failing_tests = sorted({t for g in error_groups for t in g["tests"]})

    prev_run_id = store.previous_run_for(command_str)
    try:
        prev_meta = store.load_meta(prev_run_id) if prev_run_id else None
    except (FileNotFoundError, ValueError):
        prev_meta = None
    curr_sig_map = {
        e["signature"]: {"message": e["message"], "count": e["count"]} for e in events_index
    }
    diff_block = (
        compute_diff(
            prev_meta, exit_code, parsed.get("numerical_summary"), curr_sig_map, failing_tests
        )
        if prev_meta and prev_meta.get("status") != "running"
        else None
    )

    small = line_count <= SMALL_OUTPUT_LINES
    raw_tail = None
    if status != "pass" and not error_events and not small:
        # nothing recognisable extracted: never hand back an empty failure
        tail = parsed["tail"]
        raw_tail = {"start_line": line_count - len(tail) + 1, "lines": tail}

    root = error_events[:ROOT_EVENT_CAP]
    repeated = error_events[ROOT_EVENT_CAP : ROOT_EVENT_CAP + REPEATED_EVENT_CAP]
    next_event_id = events_index[0]["index"] if events_index else 1

    summary = {
        "command": command_str,
        "exit_code": exit_code,
        "duration_seconds": round(duration, 3),
        "status": status,
        "note": note,
        "profile": profile_name,
        "root_events": [_public_event(e) for e in root],
        "warnings": [_public_event(e) for e in warning_events[:WARNING_CAP]],
        "repeated_events": [_public_event(e) for e in repeated],
        "omitted_error_groups": max(0, len(error_events) - ROOT_EVENT_CAP - REPEATED_EVENT_CAP),
        "omitted_warning_groups": max(0, len(warning_events) - WARNING_CAP),
        "failing_tests_count": len(failing_tests),
        "failing_tests": failing_tests[:FAILING_TESTS_SHOWN],
        "raw_tail": raw_tail,
        "raw_output": parsed["head"] if small else None,
        "changed_since_previous_run": diff_block,
        "numerical_summary": parsed.get("numerical_summary"),
        "artifacts": parsed.get("artifacts", []),
        "raw_output_id": run_id,
        "raw_line_count": line_count,
        "retrieval_handles": [
            f"{_handle_prefix} get {run_id} --lines <start>:<end>",
            f"{_handle_prefix} event {run_id} --event {next_event_id}",
            f"{_handle_prefix} grep {run_id} '<pattern>'",
            f"{_handle_prefix} show {run_id} --json",
        ],
    }

    meta = dict(summary)
    meta["group_signatures"] = {
        e["signature"]: {"message": e["message"], "count": e["count"], "severity": e["severity"]}
        for e in events_index
    }
    meta["events_index"] = events_index
    meta["failing_test_ids"] = failing_tests
    meta["timestamp"] = time.time()
    if signal_info:
        meta["signal"] = signal_info["signal"]
    # index first: from then on it is its command's latest run, so a
    # concurrent gc can never evict it (until then its provisional meta says
    # "running")
    store.record_signature(command_str, run_id)
    store.save_meta(run_id, meta)
    return summary


# ---------------------------------------------------------------- human rendering


def render_human(summary: dict) -> str:
    out = []
    out.append(f"$ {summary['command']}")
    out.append(
        f"exit={summary['exit_code']} duration={summary['duration_seconds']}s "
        f"status={summary['status'].upper()} profile={summary['profile']}"
    )
    out.append(
        f"raw_output_id={summary['raw_output_id']} ({summary['raw_line_count']} lines captured, stored)"
    )
    if summary.get("note"):
        out.append(f"note: {summary['note']}")
    out.append("")

    diff = summary.get("changed_since_previous_run")
    if diff:
        out.append(f"Changed since previous run ({diff['previous_run']}):")
        if diff["new_events"]:
            out.append("  New:")
            out += [f"    + {e['message']} (x{e['count']})" for e in diff["new_events"]]
        if diff["resolved_events"]:
            out.append("  Resolved:")
            out += [f"    - {e['message']} (was x{e['count']})" for e in diff["resolved_events"]]
        if diff["count_changes"]:
            out.append("  Count changed:")
            out += [
                f"    ~ {e['message']}: {e['before']} -> {e['after']}"
                for e in diff["count_changes"]
            ]
        for key, label, mark in (
            ("newly_failing", "Newly failing tests", "+"),
            ("newly_passing", "Newly passing tests", "-"),
        ):
            n = diff.get(f"{key}_tests_count", 0)
            if n:
                out.append(f"  {label} ({n}):")
                out += [f"    {mark} {t}" for t in diff[f"{key}_tests"]]
                if n > len(diff[f"{key}_tests"]):
                    out.append(f"    (+{n - len(diff[f'{key}_tests'])} more)")
        if not (
            diff["new_events"]
            or diff["resolved_events"]
            or diff["count_changes"]
            or diff.get("newly_failing_tests_count")
            or diff.get("newly_passing_tests_count")
        ):
            out.append("  No structural changes.")
        b, a = diff.get("numerical_summary_before"), diff.get("numerical_summary_after")
        if b and a and ("final_residual" in b or "final_residual" in a):
            out.append(
                f"  Residual: {b.get('final_residual')} -> {a.get('final_residual')} ({a.get('trend')})"
            )
        out.append("")

    raw = summary.get("raw_output")
    if raw is not None:
        # small output: the output itself is cheaper than a structured summary
        out.append(f"Output ({len(raw)} lines, ANSI stripped):")
        out += [f"  {l}" for l in raw]
        out.append("")
        ns = summary.get("numerical_summary")
        if ns:
            out.append("Numerical summary: " + ", ".join(f"{k}: {v}" for k, v in ns.items()))
        if summary["status"] == "pass" and not summary["root_events"]:
            out.append("No errors detected.")
        return "\n".join(out).rstrip("\n")

    if summary["root_events"]:
        out.append("Root error(s):")
        for e in summary["root_events"]:
            out.append(f"  {e['message']}")
            extra = f"lines {e['lines']}, x{e['count']}"
            if e.get("user_location"):
                extra += f", from user code at {e['user_location']}"
            if "affected_tests_count" in e:
                extra += f", affected tests: {e['affected_tests_count']}"
            out.append(f"    ({extra})")
        out.append("")

    more_hint = f"see `{_handle_prefix} show {summary['raw_output_id']} --json`"
    if summary["repeated_events"]:
        out.append("Secondary failure groups:")
        out += [
            f"  - {e['message']} x{e['count']}"
            + (f" (from user code at {e['user_location']})" if e.get("user_location") else "")
            for e in summary["repeated_events"]
        ]
        if summary.get("omitted_error_groups"):
            out.append(f"  (+{summary['omitted_error_groups']} more groups, {more_hint})")
        out.append("")

    n_failing = summary.get("failing_tests_count", 0)
    if n_failing:
        shown = summary["failing_tests"]
        line = f"Failing tests ({n_failing}): " + ", ".join(shown)
        if n_failing > len(shown):
            line += f" (+{n_failing - len(shown)} more)"
        out.append(line)
        out.append("")

    if summary["warnings"]:
        n_warn = len(summary["warnings"]) + summary.get("omitted_warning_groups", 0)
        out.append(f"Warnings: {n_warn} unique")
        out += [f"  - {e['message']} x{e['count']}" for e in summary["warnings"]]
        if summary.get("omitted_warning_groups"):
            out.append(f"  (+{summary['omitted_warning_groups']} more groups, {more_hint})")
        out.append("")

    tail = summary.get("raw_tail")
    if tail:
        n = len(tail["lines"])
        out.append(f"No error events extracted; last {n} lines of output (ANSI stripped):")
        out += [f"  {tail['start_line'] + k}: {l}" for k, l in enumerate(tail["lines"])]
        out.append("")

    ns = summary.get("numerical_summary")
    if ns:
        out.append("Numerical summary:")
        out += [f"  {k}: {v}" for k, v in ns.items()]
        out.append("")

    if summary["artifacts"]:
        out.append("Artifacts: " + ", ".join(summary["artifacts"]))
        out.append("")

    if summary["status"] == "pass" and not summary["root_events"]:
        out.append("No errors detected.")
        out.append("")

    out.append("Retrieval:")
    out += [f"  {h}" for h in summary["retrieval_handles"]]
    return "\n".join(out)


# ---------------------------------------------------------------- commands


class _SignalForwarder:
    """While the child runs: forward SIGINT/SIGTERM to its process group
    (a second signal escalates to SIGKILL), remembering the first one."""

    SIGNALS = (signal.SIGINT, signal.SIGTERM)

    def __init__(self, pgid: int):
        self.pgid = pgid
        self.received = None
        self.previous = {}

    def _handle(self, signum, frame):
        sig = signum if self.received is None else signal.SIGKILL
        if self.received is None:
            self.received = signum
        try:
            os.killpg(self.pgid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    def __enter__(self):
        for s in self.SIGNALS:
            self.previous[s] = signal.signal(s, self._handle)
        return self

    def __exit__(self, *exc):
        for s, h in self.previous.items():
            signal.signal(s, h)
        return False


def _signal_name(num: int) -> str:
    try:
        return signal.Signals(num).name
    except ValueError:
        return f"SIG{num}"


def _finish(args, store: Store, run_id: str, result: dict) -> None:
    store.gc(protect=(run_id,))
    print(json.dumps(result, indent=2) if args.json else render_human(result))


def cmd_run(args, store: Store) -> int:
    cmd_list = args.command
    if cmd_list and cmd_list[0] == "--":
        cmd_list = cmd_list[1:]
    if not cmd_list:
        print("error: no command given (usage: compost run -- <command...>)", file=sys.stderr)
        return 2
    command_str = shlex.join(cmd_list)
    # the run dir exists before the child starts, and the child writes
    # stdout+stderr (merged) straight into raw.txt: partial output is always
    # on disk, whatever happens to compost itself
    run_id = store.next_run_id(command_str)
    start = time.perf_counter()
    timed_out = None
    with open(store.raw_path(run_id), "wb") as raw:
        try:
            proc = subprocess.Popen(
                cmd_list,
                stdin=subprocess.DEVNULL,
                stdout=raw,
                stderr=subprocess.STDOUT,
                # own process group: signals/timeouts reach the whole tree
                start_new_session=True,
            )
        except (FileNotFoundError, PermissionError) as e:
            code, what = (127, "command not found") if isinstance(
                e, FileNotFoundError
            ) else (126, "permission denied")
            print(f"compost: {what}: {cmd_list[0]}", file=sys.stderr)
            meta = store.load_meta(run_id)
            meta.update(status="error", exit_code=code, note=f"{what}: {cmd_list[0]}")
            store.save_meta(run_id, meta)
            return code
        with _SignalForwarder(proc.pid) as fwd:
            try:
                proc.wait(timeout=args.timeout)
            except subprocess.TimeoutExpired:
                timed_out = args.timeout
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    proc.kill()
                proc.wait()
    duration = time.perf_counter() - start
    signal_info = None
    if timed_out is not None:
        exit_code = TIMEOUT_EXIT
    elif fwd.received is not None:
        name = _signal_name(fwd.received)
        exit_code = 128 + fwd.received
        signal_info = {
            "signal": name,
            "status": "killed",
            "note": f"compost received {name}; forwarded to the command's process group",
        }
    elif proc.returncode < 0:
        # shell convention: killed by signal N -> 128+N (subprocess reports -N)
        name = _signal_name(-proc.returncode)
        exit_code = 128 - proc.returncode
        signal_info = {"signal": name, "status": "signaled", "note": f"command died from {name}"}
    else:
        exit_code = proc.returncode
    result = process_and_store(
        store, run_id, command_str, exit_code, duration, args.profile, timed_out, signal_info
    )
    _finish(args, store, run_id, result)
    return exit_code


def cmd_ingest(args, store: Store) -> int:
    if not (args.stdin or args.file):
        print("error: --file PATH or --stdin required", file=sys.stderr)
        return 2
    if args.file and not Path(args.file).is_file():
        print(f"error: no such file: {args.file}", file=sys.stderr)
        return 1
    command_str = args.command or (args.file or "<stdin>")
    run_id = store.next_run_id(command_str)
    with open(store.raw_path(run_id), "wb") as raw:
        if args.stdin:
            shutil.copyfileobj(sys.stdin.buffer, raw)
        else:
            with open(args.file, "rb") as src:
                shutil.copyfileobj(src, raw)
    result = process_and_store(store, run_id, command_str, args.exit_code, 0.0, args.profile)
    _finish(args, store, run_id, result)
    return 0


def _parse_range(spec: str):
    if spec == "all":
        return 1, None, False
    parts = spec.split(":")
    if len(parts) == 1:
        a = b = int(parts[0])  # a single line number
    elif len(parts) == 2:
        a, b = int(parts[0]), int(parts[1])
    else:
        raise ValueError
    truncated = (b - a + 1) > MAX_GET_LINES
    if truncated:
        b = a + MAX_GET_LINES - 1
    return max(1, a), b, truncated


def cmd_get(args, store: Store) -> int:
    try:
        a, b, truncated = _parse_range(args.lines)
    except ValueError:
        print(
            f"error: invalid --lines {args.lines!r} (expected N, A:B or 'all')", file=sys.stderr
        )
        return 2
    for i, line in enumerate(store.iter_raw(args.run_id), start=1):
        if b is not None and i > b:
            break
        if i >= a:
            print(f"{i}: {line}")
    if truncated:
        print(
            f"... truncated to {MAX_GET_LINES} lines; request another range or --lines all for the rest",
            file=sys.stderr,
        )
    return 0


def _print_windows(store: Store, run_id: str, centers: list, context: int, sep: str | None):
    """Print +-context lines around each center line (streamed: only the
    needed lines are held)."""
    wanted = set()
    for c in centers:
        wanted.update(range(max(1, c - context), c + context + 1))
    last = max(wanted) if wanted else 0
    got = {}
    for i, line in enumerate(store.iter_raw(run_id), start=1):
        if i > last:
            break
        if i in wanted:
            got[i] = line
    for k, c in enumerate(centers):
        for i in range(max(1, c - context), c + context + 1):
            if i in got:
                print(f"{'>>' if i == c else '  '} {i}: {got[i]}")
        if sep is not None and k < len(centers) - 1:
            print(sep)


def cmd_event(args, store: Store) -> int:
    meta = store.load_meta(args.run_id)
    entry = next((e for e in meta.get("events_index", []) if e["index"] == args.event), None)
    if entry is None:
        print(f"error: no event {args.event} in {args.run_id}", file=sys.stderr)
        return 1
    print(f"event {entry['index']}: {entry['message']}")
    print(f"severity={entry['severity']} count={entry['count']}")
    if entry.get("user_location"):
        print(f"user code location: {entry['user_location']}")
    if entry.get("tests"):
        n = entry.get("tests_count", len(entry["tests"]))
        print(f"affected_tests ({n}): {', '.join(entry['tests'][:50])}")
    print(f"occurs at lines: {entry.get('line_ranges') or compact_ranges(entry['lines'])}")
    print("---")
    _print_windows(store, args.run_id, entry["lines"][:5], args.context, "...")
    return 0


def cmd_grep(args, store: Store) -> int:
    try:
        pattern = re.compile(re.escape(args.pattern) if args.fixed_strings else args.pattern)
    except re.error as e:
        print(f"error: invalid regex {args.pattern!r}: {e} (use -F for a literal)", file=sys.stderr)
        return 2
    hits = []
    for i, line in enumerate(store.iter_raw(args.run_id), start=1):
        if pattern.search(line):
            hits.append(i)
            if len(hits) >= args.max:
                break
    if not hits:
        print("no matches", file=sys.stderr)
        return 1
    _print_windows(store, args.run_id, hits, args.context, None)
    if len(hits) >= args.max:
        print(f"... stopped at {args.max} matches; refine pattern for more", file=sys.stderr)
    return 0


def cmd_diff(args, store: Store) -> int:
    meta_a = store.load_meta(args.run_a)
    meta_b = store.load_meta(args.run_b)
    sig_b = meta_b.get("group_signatures", {})
    diff = compute_diff(
        meta_a,
        meta_b["exit_code"],
        meta_b.get("numerical_summary"),
        sig_b,
        meta_b.get("failing_test_ids", []),
    )
    print(json.dumps(diff, indent=2))
    return 0


def cmd_list(args, store: Store) -> int:
    for m in store.list_runs(args.limit):
        print(
            f"{m['raw_output_id']}  {m['status']:5s}  exit={str(m['exit_code']):<4} "
            f"{(m.get('command') or '')[:80]}"
        )
    return 0


def cmd_show(args, store: Store) -> int:
    meta = store.load_meta(args.run_id)
    if args.json or "retrieval_handles" not in meta:
        # in-progress / aborted runs only have a provisional meta
        print(json.dumps(meta, indent=2))
    else:
        print(render_human(meta))
    return 0


def cmd_gc(args, store: Store) -> int:
    res = store.gc(args.keep_per_command, args.max_store_mb)
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        mb = 1024 * 1024
        print(
            f"evicted {len(res['evicted'])} run(s)"
            + (f": {', '.join(res['evicted'])}" if res["evicted"] else "")
        )
        print(
            f"store: {res['bytes_before'] / mb:.1f} MB -> {res['bytes_after'] / mb:.1f} MB "
            f"(keep_per_command={res['keep_per_command']}, max_store_mb={res['max_store_mb']:g})"
        )
    return 0


# ---------------------------------------------------------------- CLI wiring


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="compost")
    p.add_argument(
        "--store", default=None, help="override store directory (default: <git root, else cwd>/.compost)"
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="execute a command and store a compact summary")
    p_run.add_argument("--profile", default=None, choices=list(PARSERS))
    p_run.add_argument("--json", action="store_true")
    p_run.add_argument(
        "--timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help="kill the command's process group after SECONDS (status TIMEOUT, exit 124)",
    )
    p_run.add_argument("command", nargs=argparse.REMAINDER)

    p_ingest = sub.add_parser("ingest", help="summarize an already-captured log file/stdin")
    p_ingest.add_argument("--file", default=None)
    p_ingest.add_argument("--stdin", action="store_true")
    p_ingest.add_argument(
        "--command", default=None, help="label used for signature grouping/diffing"
    )
    p_ingest.add_argument("--exit-code", type=int, default=0)
    p_ingest.add_argument("--profile", default=None, choices=list(PARSERS))
    p_ingest.add_argument("--json", action="store_true")

    p_get = sub.add_parser("get", help="fetch raw lines by range")
    p_get.add_argument("run_id")
    p_get.add_argument("--lines", required=True, help="N, A:B inclusive, or 'all'")

    p_event = sub.add_parser(
        "event", help="fetch full detail + raw excerpt for one clustered event"
    )
    p_event.add_argument("run_id")
    p_event.add_argument("--event", type=int, required=True)
    p_event.add_argument("--context", type=int, default=3)

    p_grep = sub.add_parser("grep", help="regex search the raw output")
    p_grep.add_argument("run_id")
    p_grep.add_argument("pattern")
    p_grep.add_argument(
        "-F", "--fixed-strings", action="store_true", help="treat pattern as a literal string"
    )
    p_grep.add_argument("--context", type=int, default=0)
    p_grep.add_argument("--max", type=int, default=200)

    p_diff = sub.add_parser("diff", help="explicit diff between two stored runs")
    p_diff.add_argument("run_a")
    p_diff.add_argument("run_b")

    p_list = sub.add_parser("list", help="list stored runs")
    p_list.add_argument("--limit", type=int, default=20)

    p_show = sub.add_parser("show", help="re-print a stored run's summary")
    p_show.add_argument("run_id")
    p_show.add_argument("--json", action="store_true")

    p_gc = sub.add_parser(
        "gc", help="apply retention now (also runs automatically after every run/ingest)"
    )
    p_gc.add_argument(
        "--keep-per-command", type=int, default=None, help="default: config.json or 10"
    )
    p_gc.add_argument(
        "--max-store-mb", type=float, default=None, help="default: config.json or 200"
    )
    p_gc.add_argument("--json", action="store_true")

    return p


def main(argv=None) -> int:
    global _handle_prefix
    args = build_parser().parse_args(argv)
    if args.store:
        _handle_prefix = f"{BIN} --store {Path(args.store).resolve()}"
    store = Store(find_store_root(args.store))
    handlers = {
        "run": cmd_run,
        "ingest": cmd_ingest,
        "get": cmd_get,
        "event": cmd_event,
        "grep": cmd_grep,
        "diff": cmd_diff,
        "list": cmd_list,
        "show": cmd_show,
        "gc": cmd_gc,
    }
    try:
        return handlers[args.cmd](args, store)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
