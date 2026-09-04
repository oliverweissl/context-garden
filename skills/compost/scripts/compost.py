#!/usr/bin/env python3
"""compost: capture, cluster and compact-summarize large tool output.

See ../SKILL.md for the agent-facing workflow and ../references/schema.md
for the full output schema. Stdlib-only, no network access, no LLM calls.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from co_cluster import cluster_events, compact_ranges
from co_profiles import PARSERS, detect_profile, scan_explicit_markers
from co_store import Store, find_store_root

MAX_GET_LINES = 400
ROOT_EVENT_CAP = 1
REPEATED_EVENT_CAP = 10
WARNING_CAP = 5


# ---------------------------------------------------------------- summary building

def _derive_status(exit_code: int, error_groups: list[dict], parsed: dict) -> str:
    ns = parsed.get("numerical_summary")
    if ns and ns.get("nan_or_inf_detected"):
        return "fail"
    if exit_code != 0 or error_groups:
        return "fail"
    return "pass"


def _public_event(e: dict) -> dict:
    out = {
        "event_id": e["index"],
        "message": e["message"],
        "count": e["count"],
        "lines": compact_ranges(e["lines"]),
    }
    if e.get("tests"):
        out["affected_tests_count"] = len(e["tests"])
        out["affected_tests"] = e["tests"][:20]
    return out


def compute_diff(prev_meta: dict, exit_code: int, numerical_summary: dict | None, curr_sig_map: dict) -> dict:
    prev_sigs = prev_meta.get("group_signatures", {})
    new_events, resolved_events, count_changes = [], [], []
    for sig, info in curr_sig_map.items():
        if sig not in prev_sigs:
            new_events.append(info)
        elif prev_sigs[sig]["count"] != info["count"]:
            count_changes.append({"message": info["message"], "before": prev_sigs[sig]["count"], "after": info["count"]})
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
    if prev_meta.get("numerical_summary") and numerical_summary:
        diff["numerical_summary_before"] = prev_meta["numerical_summary"]
        diff["numerical_summary_after"] = numerical_summary
    return diff


def process_and_store(store: Store, command_str: str, text: str, exit_code: int, duration: float, profile_override: str | None) -> dict:
    run_id = store.next_run_id()
    store.save_raw(run_id, text)
    lines = text.splitlines()

    profile_name = profile_override or detect_profile(command_str, text)
    parser = PARSERS.get(profile_name, PARSERS["generic"])
    parsed = parser(lines, exit_code)

    seen_lines = {e["line"] for e in parsed["events"]}
    for e in scan_explicit_markers(lines):
        if e["line"] not in seen_lines:
            parsed["events"].append(e)
            seen_lines.add(e["line"])
    parsed["events"].sort(key=lambda e: e["line"])

    groups = cluster_events(parsed["events"])
    groups_sorted = sorted(groups, key=lambda g: (-g["count"], g["representative"]["line"]))

    events_index = []
    for idx, g in enumerate(groups_sorted, start=1):
        member_lines = sorted({m["line"] for m in g["members"]})
        tests = sorted({m["test"] for m in g["members"] if m.get("test")})
        events_index.append(
            {
                "index": idx,
                "message": g["representative"]["message"][:300],
                "severity": g["representative"]["severity"],
                "count": g["count"],
                "lines": member_lines,
                "signature": g["signature"],
                "tests": tests,
            }
        )

    error_events = [e for e in events_index if e["severity"] == "error"]
    warning_events = [e for e in events_index if e["severity"] == "warning"]
    error_groups_for_status = [g for g in groups_sorted if g["representative"]["severity"] == "error"]

    status = _derive_status(exit_code, error_groups_for_status, parsed)

    prev_run_id = store.previous_run_for(command_str)
    prev_meta = store.load_meta(prev_run_id) if prev_run_id else None
    curr_sig_map = {e["signature"]: {"message": e["message"], "count": e["count"]} for e in events_index}
    diff_block = compute_diff(prev_meta, exit_code, parsed.get("numerical_summary"), curr_sig_map) if prev_meta else None

    root = error_events[:ROOT_EVENT_CAP]
    repeated = error_events[ROOT_EVENT_CAP : ROOT_EVENT_CAP + REPEATED_EVENT_CAP]
    next_event_id = events_index[0]["index"] if events_index else 1

    summary = {
        "command": command_str,
        "exit_code": exit_code,
        "duration_seconds": round(duration, 3),
        "status": status,
        "profile": profile_name,
        "root_events": [_public_event(e) for e in root],
        "warnings": [_public_event(e) for e in warning_events[:WARNING_CAP]],
        "repeated_events": [_public_event(e) for e in repeated],
        "changed_since_previous_run": diff_block,
        "numerical_summary": parsed.get("numerical_summary"),
        "artifacts": parsed.get("artifacts", []),
        "raw_output_id": run_id,
        "raw_line_count": len(lines),
        "retrieval_handles": [
            f"compost get {run_id} --lines <start>:<end>",
            f"compost event {run_id} --event {next_event_id}",
            f"compost grep {run_id} '<pattern>'",
        ],
    }

    meta = dict(summary)
    meta["group_signatures"] = {e["signature"]: {"message": e["message"], "count": e["count"], "severity": e["severity"]} for e in events_index}
    meta["events_index"] = events_index
    meta["timestamp"] = time.time()
    store.save_meta(run_id, meta)
    store.record_signature(command_str, run_id)
    return summary


# ---------------------------------------------------------------- human rendering

def render_human(summary: dict) -> str:
    out = []
    out.append(f"$ {summary['command']}")
    out.append(
        f"exit={summary['exit_code']} duration={summary['duration_seconds']}s "
        f"status={summary['status'].upper()} profile={summary['profile']}"
    )
    out.append(f"raw_output_id={summary['raw_output_id']} ({summary['raw_line_count']} lines captured, stored)")
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
            out += [f"    ~ {e['message']}: {e['before']} -> {e['after']}" for e in diff["count_changes"]]
        if not (diff["new_events"] or diff["resolved_events"] or diff["count_changes"]):
            out.append("  No structural changes.")
        if "numerical_summary_before" in diff:
            b, a = diff["numerical_summary_before"], diff["numerical_summary_after"]
            out.append(f"  Residual: {b.get('final_residual')} -> {a.get('final_residual')} ({a.get('trend')})")
        out.append("")

    if summary["root_events"]:
        out.append("Root error(s):")
        for e in summary["root_events"]:
            out.append(f"  {e['message']}")
            extra = f"lines {e['lines']}, x{e['count']}"
            if "affected_tests_count" in e:
                extra += f", affected tests: {e['affected_tests_count']}"
            out.append(f"    ({extra})")
        out.append("")

    if summary["repeated_events"]:
        out.append("Secondary failure groups:")
        out += [f"  - {e['message']} x{e['count']}" for e in summary["repeated_events"]]
        out.append("")

    if summary["warnings"]:
        out.append(f"Warnings: {len(summary['warnings'])} unique")
        out += [f"  - {e['message']} x{e['count']}" for e in summary["warnings"]]
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

def cmd_run(args, store: Store) -> int:
    cmd_list = args.command
    if cmd_list and cmd_list[0] == "--":
        cmd_list = cmd_list[1:]
    if not cmd_list:
        print("error: no command given (usage: compost run -- <command...>)", file=sys.stderr)
        return 2
    command_str = " ".join(cmd_list)
    start = time.perf_counter()
    proc = subprocess.run(cmd_list, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    duration = time.perf_counter() - start
    text = proc.stdout.decode(errors="replace")
    result = process_and_store(store, command_str, text, proc.returncode, duration, args.profile)
    print(json.dumps(result, indent=2) if args.json else render_human(result))
    return proc.returncode


def cmd_ingest(args, store: Store) -> int:
    if args.stdin:
        text = sys.stdin.read()
    elif args.file:
        text = Path(args.file).read_text(errors="replace")
    else:
        print("error: --file PATH or --stdin required", file=sys.stderr)
        return 2
    command_str = args.command or (args.file or "<stdin>")
    result = process_and_store(store, command_str, text, args.exit_code, 0.0, args.profile)
    print(json.dumps(result, indent=2) if args.json else render_human(result))
    return 0


def cmd_get(args, store: Store) -> int:
    lines = store.load_raw(args.run_id).splitlines()
    if args.lines == "all":
        a, b = 1, len(lines)
        truncated = False
    else:
        a_s, b_s = args.lines.split(":")
        a, b = int(a_s), int(b_s)
        truncated = (b - a + 1) > MAX_GET_LINES
        if truncated:
            b = a + MAX_GET_LINES - 1
    a = max(1, a)
    b = min(len(lines), b)
    for i in range(a, b + 1):
        print(f"{i}: {lines[i - 1]}")
    if truncated:
        print(f"... truncated to {MAX_GET_LINES} lines; request another range or --lines all for the rest", file=sys.stderr)
    return 0


def cmd_event(args, store: Store) -> int:
    meta = store.load_meta(args.run_id)
    entry = next((e for e in meta["events_index"] if e["index"] == args.event), None)
    if entry is None:
        print(f"error: no event {args.event} in {args.run_id}", file=sys.stderr)
        return 1
    raw_lines = store.load_raw(args.run_id).splitlines()
    print(f"event {entry['index']}: {entry['message']}")
    print(f"severity={entry['severity']} count={entry['count']}")
    if entry.get("tests"):
        print(f"affected_tests ({len(entry['tests'])}): {', '.join(entry['tests'][:50])}")
    print(f"occurs at lines: {compact_ranges(entry['lines'])}")
    print("---")
    for ln in entry["lines"][:5]:
        lo = max(1, ln - args.context)
        hi = min(len(raw_lines), ln + args.context)
        for i in range(lo, hi + 1):
            marker = ">>" if i == ln else "  "
            print(f"{marker} {i}: {raw_lines[i - 1]}")
        print("...")
    return 0


def cmd_grep(args, store: Store) -> int:
    raw_lines = store.load_raw(args.run_id).splitlines()
    pattern = re.compile(args.pattern)
    hits = 0
    for i, line in enumerate(raw_lines, start=1):
        if pattern.search(line):
            lo = max(1, i - args.context)
            hi = min(len(raw_lines), i + args.context)
            for j in range(lo, hi + 1):
                marker = ">>" if j == i else "  "
                print(f"{marker} {j}: {raw_lines[j - 1]}")
            hits += 1
            if hits >= args.max:
                print(f"... stopped at {args.max} matches; refine pattern for more", file=sys.stderr)
                break
    if hits == 0:
        print("no matches", file=sys.stderr)
        return 1
    return 0


def cmd_diff(args, store: Store) -> int:
    meta_a = store.load_meta(args.run_a)
    meta_b = store.load_meta(args.run_b)
    sig_b = meta_b.get("group_signatures", {})
    diff = compute_diff(meta_a, meta_b["exit_code"], meta_b.get("numerical_summary"), sig_b)
    print(json.dumps(diff, indent=2))
    return 0


def cmd_list(args, store: Store) -> int:
    for m in store.list_runs(args.limit):
        print(f"{m['raw_output_id']}  {m['status']:5s}  exit={m['exit_code']:<4} {m['command'][:80]}")
    return 0


def cmd_show(args, store: Store) -> int:
    meta = store.load_meta(args.run_id)
    print(json.dumps(meta, indent=2) if args.json else render_human(meta))
    return 0


# ---------------------------------------------------------------- CLI wiring

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="compost")
    p.add_argument("--store", default=None, help="override store directory (default: ./.compost)")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="execute a command and store a compact summary")
    p_run.add_argument("--profile", default=None, choices=list(PARSERS))
    p_run.add_argument("--json", action="store_true")
    p_run.add_argument("command", nargs=argparse.REMAINDER)

    p_ingest = sub.add_parser("ingest", help="summarize an already-captured log file/stdin")
    p_ingest.add_argument("--file", default=None)
    p_ingest.add_argument("--stdin", action="store_true")
    p_ingest.add_argument("--command", default=None, help="label used for signature grouping/diffing")
    p_ingest.add_argument("--exit-code", type=int, default=0)
    p_ingest.add_argument("--profile", default=None, choices=list(PARSERS))
    p_ingest.add_argument("--json", action="store_true")

    p_get = sub.add_parser("get", help="fetch raw lines by range")
    p_get.add_argument("run_id")
    p_get.add_argument("--lines", required=True, help="A:B inclusive, or 'all'")

    p_event = sub.add_parser("event", help="fetch full detail + raw excerpt for one clustered event")
    p_event.add_argument("run_id")
    p_event.add_argument("--event", type=int, required=True)
    p_event.add_argument("--context", type=int, default=3)

    p_grep = sub.add_parser("grep", help="regex search the raw output")
    p_grep.add_argument("run_id")
    p_grep.add_argument("pattern")
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

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
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
    }
    try:
        return handlers[args.cmd](args, store)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
