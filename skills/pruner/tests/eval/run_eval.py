#!/usr/bin/env python3
"""Selection-quality eval for pruner.

Each case in cases.json is a task (and optionally error text) against one of
the committed fixture repos, plus the ground-truth `file:start-end` ranges a
good slice must contain. For every case this runs the real CLI
(`pruner select --json`) on a scratch copy of the fixture and reports:

  recall     fraction of ground-truth ranges covered (>= COVER_FRACTION of
             the range's lines fall inside selected chunks)
  precision  fraction of selected tokens (unique lines, overlap counted once)
             that fall inside a ground-truth range
  tight      recall again at a tight budget (~1.3x the ground truth's own
             tokens), which measures ranking rather than budget slack

Exits nonzero if mean recall (either budget), or any single case's recall,
drops below the committed baseline (baseline.json). `--update-baseline` rewrites it.
Stdlib only, no network.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent.parent
PRUNER = Path(os.environ.get("PRUNER_EVAL_SCRIPT", SKILL / "scripts" / "pruner.py"))
FIXTURES = SKILL / "tests" / "fixtures"
CASES = HERE / "cases.json"
BASELINE = HERE / "baseline.json"
COVER_FRACTION = 0.9
EPS = 1e-9


def _parse_range(r: str) -> tuple[str, int, int]:
    path, _, span = r.rpartition(":")
    a, _, b = span.partition("-")
    return path, int(a), int(b)


def _line_tokens(repo: Path, rel: str, cache: dict) -> list[int]:
    if rel not in cache:
        try:
            lines = (repo / rel).read_text(errors="replace").splitlines()
        except OSError:
            lines = []
        cache[rel] = [max(1, round((len(line) + 1) / 4)) for line in lines]
    return cache[rel]


def score_case(case: dict, result: dict, repo: Path) -> dict:
    selected: dict[str, set[int]] = {}
    for key in ("required_context", "supporting_context", "relevant_tests", "relevant_config"):
        for c in result.get(key, []):
            selected.setdefault(c["file"], set()).update(range(c["start_line"], c["end_line"] + 1))

    truth: dict[str, set[int]] = {}
    covered, missed = 0, []
    for r in case["expect"]:
        path, a, b = _parse_range(r)
        span = set(range(a, b + 1))
        truth.setdefault(path, set()).update(span)
        hit = len(span & selected.get(path, set())) / len(span)
        if hit >= COVER_FRACTION:
            covered += 1
        else:
            missed.append(r)

    cache: dict = {}
    sel_tok = gt_tok = 0
    for path, lines in selected.items():
        toks = _line_tokens(repo, path, cache)
        for ln in lines:
            t = toks[ln - 1] if 0 < ln <= len(toks) else 1
            sel_tok += t
            if ln in truth.get(path, ()):
                gt_tok += t
    return {
        "recall": covered / len(case["expect"]),
        "precision": gt_tok / sel_tok if sel_tok else 0.0,
        "used": result.get("used_tokens", 0),
        "unique_tokens": sel_tok,
        "missed": missed,
    }


def tight_budget(case: dict) -> int:
    toks = 0
    for r in case["expect"]:
        path, a, b = _parse_range(r)
        lines = (FIXTURES / case["repo"] / path).read_text().splitlines()
        toks += max(1, round(len("\n".join(lines[a - 1 : b])) / 4))
    return max(100, math.ceil(1.3 * toks / 25) * 25)


def run_case(case: dict, work: Path, budget: int | None = None) -> tuple[dict, Path]:
    repo = work / case["repo"]
    store = work / f"store_{case['repo']}"
    cmd = [
        sys.executable,
        str(PRUNER),
        "--repo",
        str(repo),
        "--store",
        str(store),
        "select",
        "--task",
        case["task"],
        "--budget",
        str(budget or case["budget"]),
        "--json",
    ]
    if case.get("error"):
        cmd += ["--error", case["error"]]
    cmd += case.get("args", [])
    out = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"{case['id']}: pruner failed:\n{out.stderr}")
    return json.loads(out.stdout), repo


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--update-baseline", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true", help="print missed ranges")
    ap.add_argument("-k", default=None, help="only run cases whose id contains this")
    args = ap.parse_args(argv)

    cases = json.loads(CASES.read_text())["cases"]
    if args.k:
        cases = [c for c in cases if args.k in c["id"]]

    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        for name in sorted({c["repo"] for c in cases}):
            shutil.copytree(FIXTURES / name, work / name)
        for case in cases:
            result, repo = run_case(case, work)
            s = score_case(case, result, repo)
            tb = tight_budget(case)
            t = score_case(case, run_case(case, work, tb)[0], repo)
            s.update(tight=t["recall"], tight_budget=tb, tight_missed=t["missed"])
            rows.append((case, s))

    print(f"{'case':<34} {'recall':>6} {'prec':>6} {'used':>5} {'budget':>6} {'tight':>6} {'@tok':>5}")
    for case, s in rows:
        print(
            f"{case['id']:<34} {s['recall']:>6.2f} {s['precision']:>6.2f} "
            f"{s['used']:>5} {case['budget']:>6} {s['tight']:>6.2f} {s['tight_budget']:>5}"
        )
        if args.verbose and s["missed"]:
            print(f"    missed: {', '.join(s['missed'])}")
        if args.verbose and s["tight_missed"]:
            print(f"    missed @tight: {', '.join(s['tight_missed'])}")
    n = len(rows) or 1
    mean_recall = sum(s["recall"] for _, s in rows) / n
    mean_prec = sum(s["precision"] for _, s in rows) / n
    mean_tight = sum(s["tight"] for _, s in rows) / n
    print(f"{'MEAN':<34} {mean_recall:>6.2f} {mean_prec:>6.2f} {'':>5} {'':>6} {mean_tight:>6.2f}")

    current = {
        "mean_recall": round(mean_recall, 4),
        "mean_recall_tight": round(mean_tight, 4),
        "mean_precision": round(mean_prec, 4),
        "cases": {
            c["id"]: {
                "recall": round(s["recall"], 4),
                "recall_tight": round(s["tight"], 4),
                "precision": round(s["precision"], 4),
            }
            for c, s in rows
        },
    }
    if args.update_baseline:
        if args.k:
            print("refusing to write a baseline from a filtered (-k) run", file=sys.stderr)
            return 2
        BASELINE.write_text(json.dumps(current, indent=2) + "\n")
        print(f"baseline written: {BASELINE}")
        return 0

    if not BASELINE.exists():
        print("no baseline.json yet; run with --update-baseline", file=sys.stderr)
        return 1
    base = json.loads(BASELINE.read_text())
    failures = []
    if not args.k:
        for key, val in (("mean_recall", mean_recall), ("mean_recall_tight", mean_tight)):
            if key in base and val + EPS < base[key]:
                failures.append(f"{key} {val:.3f} < baseline {base[key]:.3f}")
    for c, s in rows:
        b = base["cases"].get(c["id"], {})
        for key, val in (("recall", s["recall"]), ("recall_tight", s["tight"])):
            if key in b and val + EPS < b[key]:
                failures.append(f"{c['id']}: {key} {val:.2f} < baseline {b[key]:.2f}")
    if failures:
        print("EVAL REGRESSION:\n  " + "\n  ".join(failures))
        return 1
    print(f"eval OK (baseline mean recall {base['mean_recall']:.2f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
