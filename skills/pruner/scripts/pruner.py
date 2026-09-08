#!/usr/bin/env python3
"""pruner: the smallest sufficient repository context for one task.

See ../SKILL.md for the agent-facing workflow and ../references/ for the
scoring/schema details. Stdlib-only, no network access, no LLM calls --
relevance is lexical/structural (keyword overlap + call/import graph
distance), not semantic embedding similarity. See references/scoring.md
for why that tradeoff was made.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pr_common import changed_files_from_git, estimate_tokens
from pr_index import load_or_build_index
from pr_select import (
    add_ad_hoc_chunk,
    expand_slice,
    load_slice,
    next_slice_id,
    save_slice,
    select,
)

STORE_DIRNAME = ".pruner"


def _store_dir(repo_root: Path, explicit: str | None) -> Path:
    return Path(explicit) if explicit else repo_root / STORE_DIRNAME


# ---------------------------------------------------------------- rendering


def _fmt_chunk_line(c: dict) -> str:
    loc = f"{c['file']}:{c['start_line']}-{c['end_line']}"
    score = f"{c['score']:.1f}" if c.get("score") is not None else "-"
    return f"  {loc}  [{c['chunk_kind']}, score={score}, ~{c['tokens']}tok]"


def render_human(result: dict) -> str:
    out = [
        f"task: {result['task']!r}",
        f"budget: {result['budget']} tokens  used: {result['used_tokens']} tokens  confidence: {result['confidence']}",
    ]
    if "slice_id" in result:
        out.append(f"slice_id: {result['slice_id']}")
    for label, key in (
        ("Required context", "required_context"),
        ("Supporting context", "supporting_context"),
        ("Relevant tests", "relevant_tests"),
        ("Relevant config", "relevant_config"),
    ):
        chunks = result[key]
        out.append("")
        out.append(f"{label} ({len(chunks)}):")
        if not chunks:
            out.append("  (none)")
        for c in chunks:
            out.append(_fmt_chunk_line(c))
            for r in c["reasons"][:2]:
                out.append(f"      - {r}")

    omitted = result["omitted_candidates"]
    out.append("")
    out.append(
        f"Omitted candidates ({len(omitted)} shown, likely relevant but cut for budget or low score):"
    )
    for c in omitted[:10]:
        out.append(_fmt_chunk_line(c))
    if "slice_id" in result:
        out.append("")
        out.append("Expansion: if reasoning/tests fail because something's missing,")
        out.append(
            f"  pruner expand {result['slice_id']} --add <file:start-end from the omitted list above>"
        )
        out.append(
            f"  pruner expand {result['slice_id']} --file <path> --lines A:B   # anything not listed at all"
        )
    return "\n".join(out)


# ---------------------------------------------------------------- commands


def cmd_index(args, repo_root: Path, store_dir: Path) -> int:
    index, report = load_or_build_index(repo_root, store_dir, force=args.force)
    print(
        f"indexed {report['files_indexed']} file(s): {report['files_unchanged']} unchanged (reused), "
        f"{report['files_reparsed_or_new']} (re)parsed"
    )
    print(f"symbols: {report['symbols']}  call/reference edges: {report['call_edges']}")
    return 0


def cmd_select(args, repo_root: Path, store_dir: Path) -> int:
    index, _report = load_or_build_index(repo_root, store_dir, force=False)

    changed = args.changed
    if changed is None and args.auto_changed:
        changed = changed_files_from_git(repo_root)

    error_text = args.error
    if args.error_file:
        error_text = Path(args.error_file).read_text(errors="replace")

    external_graph = None
    if args.graph:
        external_graph = json.loads(Path(args.graph).read_text())

    result = select(index, repo_root, args.task, args.budget, changed, error_text, external_graph)
    slice_id = next_slice_id(store_dir)
    save_slice(store_dir, slice_id, repo_root, result)
    result = {"slice_id": slice_id, **result}

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(render_human(result))
    return 0


def cmd_expand(args, repo_root: Path, store_dir: Path) -> int:
    if args.file:
        if not args.lines:
            print("error: --file requires --lines A:B", file=sys.stderr)
            return 2
        a, b = args.lines.split(":")
        outcome = add_ad_hoc_chunk(
            store_dir,
            args.slice_id,
            repo_root,
            args.file,
            int(a),
            int(b),
            args.reason or "manually added by agent",
            args.budget_extra,
        )
        if not outcome["added"]:
            print(f"error: {outcome['error']}", file=sys.stderr)
            return 1
        print(
            f"added {args.file}:{a}-{b} to {args.slice_id} (used_tokens now {outcome['record']['used_tokens']})"
        )
        return 0

    if not args.add:
        print("error: pass --add <chunk_id> (repeatable) or --file/--lines", file=sys.stderr)
        return 2
    outcome = expand_slice(store_dir, args.slice_id, args.add, args.budget_extra)
    for cid in outcome["promoted"]:
        print(f"promoted: {cid}")
    for cid in outcome["over_budget"]:
        print(f"skipped (over budget, use --budget-extra to allow): {cid}", file=sys.stderr)
    for cid in outcome["still_missing"]:
        print(f"unknown chunk id (not in this slice's omitted candidates): {cid}", file=sys.stderr)
    print(f"used_tokens now {outcome['record']['used_tokens']} / {outcome['record']['budget']}")
    return 0


def cmd_show(args, repo_root: Path, store_dir: Path) -> int:
    record = load_slice(store_dir, args.slice_id)
    print(json.dumps(record, indent=2) if args.json else render_human(record))
    return 0


def cmd_list(args, repo_root: Path, store_dir: Path) -> int:
    d = store_dir / "slices"
    if not d.exists():
        print("(no slices yet)")
        return 0
    for p in sorted(d.glob("s*.json")):
        record = json.loads(p.read_text())
        print(
            f"{record['slice_id']}  budget={record['budget']:<6} used={record['used_tokens']:<6} task={record['task']!r}"
        )
    return 0


# ---------------------------------------------------------------- CLI wiring


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pruner")
    p.add_argument("--repo", default=None, help="repository root (default: cwd)")
    p.add_argument(
        "--store", default=None, help="override store directory (default: <repo>/.pruner)"
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    p_index = sub.add_parser("index", help="build/refresh the repository index")
    p_index.add_argument(
        "--force", action="store_true", help="re-parse every file, ignore cached hashes"
    )

    p_select = sub.add_parser("select", help="select the smallest sufficient context for a task")
    p_select.add_argument("--task", required=True)
    p_select.add_argument("--budget", type=int, default=2000)
    p_select.add_argument("--changed", nargs="*", default=None, help="explicit changed file paths")
    p_select.add_argument(
        "--auto-changed",
        action="store_true",
        help="use `git diff --name-only HEAD` if --changed not given",
    )
    p_select.add_argument("--error", default=None, help="raw compiler error / traceback text")
    p_select.add_argument(
        "--error-file", default=None, help="path to a file containing the error/traceback"
    )
    p_select.add_argument(
        "--graph", default=None, help="path to an external {nodes,edges} JSON graph to merge in"
    )
    p_select.add_argument("--json", action="store_true")

    p_expand = sub.add_parser(
        "expand", help="progressively add specific chunks to an existing slice"
    )
    p_expand.add_argument("slice_id")
    p_expand.add_argument(
        "--add", nargs="*", default=None, help="chunk id(s) from that slice's omitted_candidates"
    )
    p_expand.add_argument("--file", default=None)
    p_expand.add_argument("--lines", default=None, help="A:B, used with --file")
    p_expand.add_argument("--reason", default=None)
    p_expand.add_argument("--budget-extra", type=int, default=None)

    p_show = sub.add_parser("show", help="re-print a saved slice")
    p_show.add_argument("slice_id")
    p_show.add_argument("--json", action="store_true")

    sub.add_parser("list", help="list saved slices")

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(args.repo).resolve() if args.repo else Path.cwd()
    store_dir = _store_dir(repo_root, args.store)

    handlers = {
        "index": cmd_index,
        "select": cmd_select,
        "expand": cmd_expand,
        "show": cmd_show,
        "list": cmd_list,
    }
    try:
        return handlers[args.cmd](args, repo_root, store_dir)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
