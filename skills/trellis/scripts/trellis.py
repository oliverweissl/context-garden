#!/usr/bin/env python3
"""trellis CLI: run a verification spec script and aggregate its results
into a Report.

A spec script is plain Python that imports from the `trellis` package and
calls its check functions -- each call executes the check immediately and
returns a CheckResult. The spec script collects these into a module-level
`RESULTS` list (and optionally `UNSUPPORTED_CLAIMS` / `REMAINING_RISKS`
lists for gaps it knows about beyond the automatic ones). This CLI just
executes that script, aggregates RESULTS into a Report, prints it, saves
it, and exits 1 on FAIL, 3 on WARN (0 with --allow-warn) -- see ../SKILL.md and
../tests/fixtures/*.py for worked examples.

`trellis lock <spec>` pins the spec's strictness in .trellis/spec.lock;
`trellis run` FAILs if the spec became looser than the lock (WARN if the
spec is unpinned). `--save-baseline` / `--baseline <sha|latest>` store and
compare per-commit metrics.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_PKG_DIR = _SCRIPT_DIR.parent
sys.path.insert(0, str(_PKG_DIR))

from trellis.schema import build_report  # noqa: E402


def _load_spec_module(spec_path: Path):
    from trellis import io as trellis_io

    trellis_io.BASE_DIR = spec_path.parent  # relative result-file paths also resolve here
    module_spec = importlib.util.spec_from_file_location(
        f"trellis_spec_{spec_path.stem}", spec_path
    )
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)  # executes the spec -- this is what runs the checks
    return module


def _run_spec(args):
    """-> (spec_path, results, module) or an int exit code on error."""
    spec_path = Path(args.spec).resolve()
    if not spec_path.exists():
        print(f"error: no such spec file: {spec_path}", file=sys.stderr)
        return 2
    module = _load_spec_module(spec_path)
    results = getattr(module, "RESULTS", None)
    if results is None:
        print(f"error: {spec_path} did not define a module-level RESULTS list", file=sys.stderr)
        return 2
    return spec_path, list(results), module


def _trellis_dir(args, spec_path: Path) -> Path:
    from trellis import lock as L

    return Path(args.trellis_dir).resolve() if args.trellis_dir else L.default_trellis_dir(spec_path)


def _lock_result(spec_path: Path, results, tdir: Path, update: bool):
    from trellis import lock as L
    from trellis.schema import CheckResult

    key = L.spec_key(spec_path)
    lock_path = tdir / "spec.lock"
    current = L.lock_entry(results)
    locked = L.read_lock(lock_path).get("specs", {}).get(key)
    name = f"spec_lock:{key}"
    if locked is None and not update:
        return CheckResult(
            name=name, status="WARN", category="implementation", metric={"lock": str(lock_path)},
            expected="spec pinned in .trellis/spec.lock", observed="unpinned spec",
            notes=f"Unpinned spec: no entry for {key} in {lock_path}. Run `trellis lock {spec_path}` "
            "and commit .trellis/spec.lock so later loosening of tolerances/orders is detected.",
        )
    diff = L.compare_to_lock(locked or {"checks": {}}, current)
    metric = {"lock": str(lock_path), **{k: len(v) for k, v in diff.items()}}
    lines = []
    for label, k in (("LOOSER", "looser"), ("tighter", "tighter"), ("added", "added"), ("changed", "changed_info")):
        lines += [f"[{label}] {d}" for d in diff[k]]
    if update:
        L.write_lock(lock_path, key, current)
        status = "WARN" if diff["looser"] else "PASS"
        notes = (
            f"--update-lock rewrote {lock_path} -- a HUMAN must review `git diff {lock_path}` before committing."
            + (" It LOOSENS the spec: " + "; ".join(diff["looser"]) if diff["looser"] else "")
        )
    elif diff["looser"]:
        status = "FAIL"
        notes = (
            "Spec is LOOSER than the committed lock -- a check was relaxed or removed. Restore it; "
            "only a human may accept this (trellis run --update-lock, then review the git diff). Diff: "
            + " | ".join(lines)
        )
    else:
        status = "PASS"
        notes = ("Matches lock; reported changes: " + " | ".join(lines)) if lines else ""
    return CheckResult(
        name=name, status=status, category="implementation", metric=metric,
        expected="no check looser than the lock", observed={k: v for k, v in diff.items() if v},
        evidence={"diff": lines}, notes=notes,
    )


def _baseline_result(spec_path: Path, results, tdir: Path, ref: str, factor: float):
    from trellis import lock as L
    from trellis.schema import CheckResult

    key = L.spec_key(spec_path)
    try:
        sha, base = L.load_baseline(tdir, ref, key)
    except (FileNotFoundError, ValueError) as e:
        return CheckResult(
            name=f"baseline:{ref}", status="WARN", category="implementation", metric={},
            expected=f"baseline {ref}", observed=None, notes=f"No baseline comparison: {e}",
        )
    cmp = L.compare_baseline(base, results, factor=factor)
    status = "FAIL" if cmp["regressions"] else "PASS"
    notes = ("Regression vs baseline " + sha[:12] + ": " + "; ".join(cmp["regressions"])) if cmp["regressions"] else ""
    if cmp["notes"]:
        notes = (notes + " " if notes else "") + "Notes: " + "; ".join(cmp["notes"])
    return CheckResult(
        name=f"baseline:{sha[:12]}", status=status, category="implementation",
        metric={"n_regressions": len(cmp["regressions"]), "factor": factor, "baseline_sha": sha},
        expected=f"no regression vs {sha[:12]} (order drop <= tol_order, errors/residuals <= {factor}x)",
        observed=cmp["regressions"] or "no regression", evidence=cmp, notes=notes,
    )


def cmd_lock(args) -> int:
    from trellis import lock as L

    loaded = _run_spec(args)
    if isinstance(loaded, int):
        return loaded
    spec_path, results, _ = loaded
    tdir = _trellis_dir(args, spec_path)
    path = L.write_lock(tdir / "spec.lock", L.spec_key(spec_path), L.lock_entry(results))
    print(f"locked {len(results)} check(s) of {L.spec_key(spec_path)} -> {path}")
    print("commit this file; loosening any pinned tolerance/order/reference later makes `trellis run` FAIL.")
    return 0


def cmd_run(args) -> int:
    from trellis import lock as L

    loaded = _run_spec(args)
    if isinstance(loaded, int):
        return loaded
    spec_path, results, module = loaded
    unsupported = getattr(module, "UNSUPPORTED_CLAIMS", [])
    risks = getattr(module, "REMAINING_RISKS", [])
    tdir = _trellis_dir(args, spec_path)
    checks = list(results)
    extra = [_lock_result(spec_path, results, tdir, args.update_lock)]
    if args.baseline:
        extra.append(_baseline_result(spec_path, results, tdir, args.baseline, args.regression_factor))
    report = build_report(checks + extra, unsupported, risks)

    if args.json:
        import json

        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.render_human())

    save_path = args.save or (spec_path.parent / ".trellis" / f"{spec_path.stem}_report.json")
    saved = report.save(save_path)
    print(f"\nsaved report: {saved}", file=sys.stderr)
    if args.save_baseline:
        root = L.git_root(spec_path)
        sha, dirty = L.git_sha(root) if root else (None, False)
        if not sha:
            print("error: --save-baseline needs the spec inside a git repository with a commit", file=sys.stderr)
            return 2
        bpath = L.save_baseline(tdir, sha, dirty, L.spec_key(spec_path), results)
        print(f"saved baseline: {bpath}" + (" (working tree dirty)" if dirty else ""), file=sys.stderr)
    return report.exit_code(allow_warn=args.allow_warn)


def cmd_list_modules(args) -> int:
    import inspect

    from trellis import io, linalg, ode, optimization, pde, stochastic, universal

    modules = [
        ("universal", universal, "Applicable to any numerical/scientific change."),
        ("linalg", linalg, "Linear systems, decompositions, matrix properties."),
        ("ode", ode, "Time integration: ODE solvers."),
        ("pde", pde, "Spatial discretization: PDE solvers, finite difference/element/volume."),
        ("optimization", optimization, "Solvers that minimize/maximize an objective."),
        ("stochastic", stochastic, "Monte Carlo, randomized algorithms, statistics."),
        ("io", io, "Load solver outputs from npy/npz/CSV/JSON files (C++/Fortran/MPI jobs)."),
    ]
    for mod_name, mod, desc in modules:
        print(f"{mod_name}  --  {desc}")
        for fn_name, fn in inspect.getmembers(mod, inspect.isfunction):
            if fn_name.startswith("_") or fn.__module__ != mod.__name__:
                continue  # skip helpers re-exported via `from .x import y` (e.g. threshold_status)
            doc = (fn.__doc__ or "").strip().splitlines()[0] if fn.__doc__ else ""
            print(f"    {fn_name}(...)  {doc}")
        print()
    return 0


def cmd_show(args) -> int:
    import json

    path = Path(args.report)
    data = json.loads(path.read_text())
    print(f"status: {data['status']}")
    for c in data["checks"]:
        print(f"  [{c['status']:4s}] {c['name']}  ({c['category']})")
    if data.get("unsupported_claims"):
        print("\nunsupported_claims:")
        for u in data["unsupported_claims"]:
            print(f"  - {u}")
    if data.get("remaining_risks"):
        print("\nremaining_risks:")
        for r in data["remaining_risks"]:
            print(f"  - {r}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="trellis")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser(
        "run", help="execute a verification spec script and report PASS/WARN/FAIL"
    )
    p_run.add_argument("spec", help="path to a Python spec script defining RESULTS")
    p_run.add_argument(
        "--save",
        default=None,
        help="report output path (default: <spec_dir>/.trellis/<spec>_report.json)",
    )
    p_run.add_argument("--json", action="store_true")
    p_run.add_argument(
        "--allow-warn",
        action="store_true",
        help="exit 0 on WARN (default: WARN exits 3, since WARN means 'not verified')",
    )
    p_run.add_argument(
        "--update-lock",
        action="store_true",
        help="HUMAN-ONLY: rewrite this spec's entry in .trellis/spec.lock (review the git diff); "
        "never use it to make a failing gate pass",
    )
    p_run.add_argument(
        "--save-baseline", action="store_true", help="store metrics under .trellis/baselines/<git-sha>.json"
    )
    p_run.add_argument("--baseline", default=None, metavar="SHA|latest", help="FAIL on regression vs a saved baseline")
    p_run.add_argument(
        "--regression-factor",
        type=float,
        default=2.0,
        help="baseline: FAIL if an error/residual metric grew by more than this factor (default 2)",
    )
    p_run.add_argument(
        "--trellis-dir", default=None, help="where spec.lock/baselines live (default: <git root>/.trellis)"
    )

    p_lock = sub.add_parser("lock", help="pin a spec's tolerances/orders/references in .trellis/spec.lock")
    p_lock.add_argument("spec")
    p_lock.add_argument("--trellis-dir", default=None, help="default: <git root>/.trellis")

    sub.add_parser("list-modules", help="list available check functions per domain module")

    p_show = sub.add_parser("show", help="re-print a saved report")
    p_show.add_argument("report")

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {"run": cmd_run, "lock": cmd_lock, "list-modules": cmd_list_modules, "show": cmd_show}
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
