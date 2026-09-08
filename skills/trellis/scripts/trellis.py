#!/usr/bin/env python3
"""trellis CLI: run a verification spec script and aggregate its results
into a Report.

A spec script is plain Python that imports from the `trellis` package and
calls its check functions -- each call executes the check immediately and
returns a CheckResult. The spec script collects these into a module-level
`RESULTS` list (and optionally `UNSUPPORTED_CLAIMS` / `REMAINING_RISKS`
lists for gaps it knows about beyond the automatic ones). This CLI just
executes that script, aggregates RESULTS into a Report, prints it, saves
it, and exits non-zero on FAIL -- see ../SKILL.md and
../tests/fixtures/*.py for worked examples.
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
    module_spec = importlib.util.spec_from_file_location(
        f"trellis_spec_{spec_path.stem}", spec_path
    )
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)  # executes the spec -- this is what runs the checks
    return module


def cmd_run(args) -> int:
    spec_path = Path(args.spec).resolve()
    if not spec_path.exists():
        print(f"error: no such spec file: {spec_path}", file=sys.stderr)
        return 2
    module = _load_spec_module(spec_path)
    results = getattr(module, "RESULTS", None)
    if results is None:
        print(f"error: {spec_path} did not define a module-level RESULTS list", file=sys.stderr)
        return 2
    unsupported = getattr(module, "UNSUPPORTED_CLAIMS", [])
    risks = getattr(module, "REMAINING_RISKS", [])
    report = build_report(results, unsupported, risks)

    if args.json:
        import json

        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.render_human())

    save_path = args.save or (spec_path.parent / ".trellis" / f"{spec_path.stem}_report.json")
    saved = report.save(save_path)
    print(f"\nsaved report: {saved}", file=sys.stderr)
    return report.exit_code()


def cmd_list_modules(args) -> int:
    import inspect

    from trellis import linalg, ode, optimization, pde, stochastic, universal

    modules = [
        ("universal", universal, "Applicable to any numerical/scientific change."),
        ("linalg", linalg, "Linear systems, decompositions, matrix properties."),
        ("ode", ode, "Time integration: ODE solvers."),
        ("pde", pde, "Spatial discretization: PDE solvers, finite difference/element/volume."),
        ("optimization", optimization, "Solvers that minimize/maximize an objective."),
        ("stochastic", stochastic, "Monte Carlo, randomized algorithms, statistics."),
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

    sub.add_parser("list-modules", help="list available check functions per domain module")

    p_show = sub.add_parser("show", help="re-print a saved report")
    p_show.add_argument("report")

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {"run": cmd_run, "list-modules": cmd_list_modules, "show": cmd_show}
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
