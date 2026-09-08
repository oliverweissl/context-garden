#!/usr/bin/env python3
"""weeder CLI: measure, mechanically refactor, and validate Skills
for token efficiency without regressing routing or functional behavior.

See ../SKILL.md for the full agent-facing workflow. Stdlib-only, no
network access, no LLM calls -- routing/function checks here are
deterministic proxies (documented in references/routing-heuristic.md),
not a replacement for spot-checking a rewrite with a real agent.
`suggest`/`apply-suggestion` don't change that: they structure workflow
step 3's judgment call as a request/answer file pair for the invoking
agent rather than calling out to any model themselves -- see
references/llm-assist.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from we_apply_suggestion import apply_suggestion
from we_audit import audit_skill, render_audit_human
from we_config import LEVELS, resolve_assist_level
from we_constraints import check_constraints_preserved
from we_optimize import optimize_skill
from we_parse import parse_skill
from we_routing import evaluate_routing
from we_suggest import build_suggestions, render_suggestions_human
from we_tokens import estimate_tokens


def cmd_audit(args) -> int:
    report = audit_skill(args.skill_dir, dup_threshold=args.dup_threshold)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_audit_human(report))
    return 0


def cmd_optimize(args) -> int:
    out_dir = args.out or f"{str(args.skill_dir).rstrip('/')}-optimized"
    result = optimize_skill(args.skill_dir, out_dir, min_tokens_to_move=args.min_tokens)
    print(f"wrote optimized copy to {result['out_dir']}")
    if not result["moves"]:
        print("no background/examples sections >= min-tokens found -- nothing mechanically movable")
    for m in result["moves"]:
        print(f"  moved '{m['heading']}' ({m['tokens_moved']} tok) -> {m['to']}")
    print(
        f"always-loaded tokens: {result['before_always_loaded_tokens']} -> {result['after_always_loaded_tokens']} "
        f"({result['reduction_pct']}% reduction from this mechanical pass alone)"
    )
    print("\nThis is ONLY the mechanical pass (progressive disclosure of background/examples).")
    print("Description shortening and duplicate-rule consolidation still need agent judgment --")
    print(
        "see the duplicate_groups / likely_unnecessary sections of `weeder audit`, and SKILL.md's workflow."
    )
    return 0


def cmd_suggest(args) -> int:
    level = resolve_assist_level(args.assist, start_dir=Path.cwd())
    result = build_suggestions(args.skill_dir, level, dup_threshold=args.dup_threshold)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(render_suggestions_human(result))
    return 0


def cmd_apply_suggestion(args) -> int:
    answer = json.loads(Path(args.answer).read_text())
    out_dir = args.out or f"{str(args.skill_dir).rstrip('/')}-optimized"
    result = apply_suggestion(args.skill_dir, out_dir, answer, dup_threshold=args.dup_threshold)
    print(f"wrote suggestion-applied copy to {result['out_dir']}")
    print(
        f"applied: {', '.join(result['applied']) or '(nothing -- answer file had no recognized keys)'}"
    )
    for w in result["warnings"]:
        print(f"  warning: {w}")
    print("\nThis only applies the answer mechanically; it doesn't validate it -- run")
    print(
        "`weeder test-routing`/`test-function`/`diff` against the output copy before accepting it"
    )
    print("(SKILL.md workflow steps 4-7, unchanged).")
    return 0


def cmd_test_routing(args) -> int:
    examples = json.loads(Path(args.examples).read_text())
    parsed = parse_skill(args.skill_dir)
    skill_name = parsed["name"]
    description = parsed["frontmatter"].get("description", "")

    competing = {}
    for comp_dir in args.competing or []:
        comp_parsed = parse_skill(comp_dir)
        competing[comp_parsed["name"]] = comp_parsed["frontmatter"].get("description", "")

    result = evaluate_routing(skill_name, description, examples, competing)
    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print(f"skill: {skill_name}  ({len(competing)} competing description(s) loaded)")
    print(
        f"routing accuracy: {result['accuracy']:.0%}  ({result['n']} example(s))"
        if result["accuracy"] is not None
        else "no examples given"
    )
    for r in result["results"]:
        mark = "OK  " if r["correct"] else "MISS"
        print(
            f"  [{mark}] {r['kind']:<10} expected={r['expected']:<10} predicted={r['predicted']!r:<20} \"{r['prompt'][:60]}\""
        )
    return 0 if (result["accuracy"] is None or result["accuracy"] >= args.min_accuracy) else 1


def cmd_test_function(args) -> int:
    before = parse_skill(args.before)
    after = parse_skill(args.after)
    result = check_constraints_preserved(before, after, threshold=args.threshold)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"constraints found in before: {result['total']}")
        print(
            f"preserved in after (found somewhere in SKILL.md+references): {result['preserved']} "
            f"({result['preserved_ratio']:.0%})"
        )
        if result["missing"]:
            print("MISSING (likely lost during editing, not just moved):")
            for m in result["missing"]:
                print(f"  - {m}")
    return 0 if result["preserved_ratio"] >= args.min_ratio else 1


def cmd_diff(args) -> int:
    before_report = audit_skill(args.before)
    after_report = audit_skill(args.after)

    print("Before")
    print(f"  Description:        {before_report['description_tokens']:>5} tokens")
    print(f"  Always-loaded:      {before_report['always_loaded_tokens']:>5} tokens")
    print(f"  References:         {before_report['total_reference_tokens']:>5} tokens")
    print()
    print("After")
    print(f"  Description:        {after_report['description_tokens']:>5} tokens")
    print(f"  Always-loaded:      {after_report['always_loaded_tokens']:>5} tokens")
    print(f"  On-demand refs:     {after_report['total_reference_tokens']:>5} tokens")
    print()
    if before_report["always_loaded_tokens"]:
        reduction = 100 * (
            1 - after_report["always_loaded_tokens"] / before_report["always_loaded_tokens"]
        )
        print(f"Always-loaded reduction: {reduction:.0f}%")

    if args.examples:
        examples = json.loads(Path(args.examples).read_text())
        competing = {}
        for comp_dir in args.competing or []:
            cp = parse_skill(comp_dir)
            competing[cp["name"]] = cp["frontmatter"].get("description", "")

        before_parsed = parse_skill(args.before)
        after_parsed = parse_skill(args.after)
        r_before = evaluate_routing(
            before_parsed["name"],
            before_parsed["frontmatter"].get("description", ""),
            examples,
            competing,
        )
        r_after = evaluate_routing(
            after_parsed["name"],
            after_parsed["frontmatter"].get("description", ""),
            examples,
            competing,
        )
        print()
        print(
            "Routing (lexical-overlap proxy, not a real-model prediction -- see references/routing-heuristic.md):"
        )
        print(
            f"  Before {r_before['accuracy']:.0%}"
            if r_before["accuracy"] is not None
            else "  Before n/a"
        )
        print(
            f"  After  {r_after['accuracy']:.0%}"
            if r_after["accuracy"] is not None
            else "  After  n/a"
        )

    func = check_constraints_preserved(parse_skill(args.before), parse_skill(args.after))
    print()
    print("Functional (constraint-preservation proxy, not a behavioral eval):")
    print(
        f"  Constraints preserved: {func['preserved']}/{func['total']} ({func['preserved_ratio']:.0%})"
    )
    if func["missing"]:
        print(f"  MISSING: {len(func['missing'])} -- see `weeder test-function` for details")

    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="weeder")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_audit = sub.add_parser(
        "audit",
        help="structural classification + token accounting + duplication + unnecessary-content flags",
    )
    p_audit.add_argument("skill_dir")
    p_audit.add_argument("--dup-threshold", type=float, default=0.6)
    p_audit.add_argument("--json", action="store_true")

    p_opt = sub.add_parser(
        "optimize", help="mechanically move background/examples sections to references/"
    )
    p_opt.add_argument("skill_dir")
    p_opt.add_argument("--out", default=None, help="output dir (default: <skill_dir>-optimized)")
    p_opt.add_argument("--min-tokens", type=int, default=20)

    p_suggest = sub.add_parser(
        "suggest",
        help="structure step 3's judgment request for the invoking agent, gated by --assist level",
    )
    p_suggest.add_argument("skill_dir")
    p_suggest.add_argument(
        "--assist",
        choices=LEVELS,
        default=None,
        help="none|slight|lot; default resolves from $CONTEXT_GARDEN_LLM_ASSIST or "
        ".context-garden/config.yaml's llm_assist.level, else none",
    )
    p_suggest.add_argument("--dup-threshold", type=float, default=0.6)
    p_suggest.add_argument("--json", action="store_true")

    p_apply = sub.add_parser(
        "apply-suggestion",
        help="mechanically apply an agent-authored answer (see `suggest`) to a copy of the skill",
    )
    p_apply.add_argument("skill_dir")
    p_apply.add_argument(
        "--answer", required=True, help="JSON file answering one or more of suggest's requests"
    )
    p_apply.add_argument("--out", default=None, help="output dir (default: <skill_dir>-optimized)")
    p_apply.add_argument(
        "--dup-threshold",
        type=float,
        default=0.6,
        help="must match the --dup-threshold used when generating the suggestion, if any "
        "(affects duplicate_consolidation's group_index numbering)",
    )

    p_route = sub.add_parser(
        "test-routing", help="evaluate routing accuracy against labeled example prompts"
    )
    p_route.add_argument("skill_dir")
    p_route.add_argument(
        "--examples",
        required=True,
        help="JSON file: {positive:[], negative:[], ambiguous:[{prompt,expected}]}",
    )
    p_route.add_argument(
        "--competing",
        nargs="*",
        default=None,
        help="other skill dirs to include as routing competitors",
    )
    p_route.add_argument("--min-accuracy", type=float, default=0.0)
    p_route.add_argument("--json", action="store_true")

    p_func = sub.add_parser(
        "test-function",
        help="check before-skill's constraints are still present somewhere in after-skill",
    )
    p_func.add_argument("before")
    p_func.add_argument("after")
    p_func.add_argument(
        "--threshold",
        type=float,
        default=0.6,
        help="keyword-coverage ratio to count a constraint as preserved",
    )
    p_func.add_argument(
        "--min-ratio",
        type=float,
        default=1.0,
        help="exit nonzero if preserved_ratio falls below this",
    )
    p_func.add_argument("--json", action="store_true")

    p_diff = sub.add_parser("diff", help="full before/after report: tokens, routing, function")
    p_diff.add_argument("before")
    p_diff.add_argument("after")
    p_diff.add_argument("--examples", default=None)
    p_diff.add_argument("--competing", nargs="*", default=None)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {
        "audit": cmd_audit,
        "optimize": cmd_optimize,
        "suggest": cmd_suggest,
        "apply-suggestion": cmd_apply_suggestion,
        "test-routing": cmd_test_routing,
        "test-function": cmd_test_function,
        "diff": cmd_diff,
    }
    try:
        return handlers[args.cmd](args)
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
