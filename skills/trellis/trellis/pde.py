"""PDE checks: mesh convergence / observed order, conservation, and a
manufactured-solution hook."""

from __future__ import annotations

import numpy as np

from ._util import record_config, threshold_status
from .convergence import convergence_check
from .schema import CheckResult, Status


@record_config
def mesh_convergence(
    solve_fn,
    resolutions=None,
    reference=None,
    expected_order: float | None = None,
    tol_order: float = 0.3,
    error_fn=None,
    name: str = "mesh_convergence",
    param_kind: str = "auto",
    errors=None,
    file_key=None,
    error_key=None,
    solution_scale: float | None = None,
    reference_key=None,
) -> CheckResult:
    """solve_fn(p) -> field for each p in `resolutions`. `param_kind`:
    "resolution" (p = cell count N, larger = finer; order fitted against
    h = 1/N), "h" (p = mesh size, smaller = finer), or "auto" (default:
    integer values >= 2 are read as cell counts, otherwise as h; the
    interpretation used is recorded in evidence["param_kind"]). This is
    the check for UC1: a stencil bug that regresses observed order from
    ~2 to ~1 shows up here even when ordinary tests at one fixed
    resolution keep passing. The default error is the grid-scaled RMS
    norm; see convergence.convergence_check.

    Real-solver outputs: `solve_fn="out/n{N}.npy"` (a file template, one
    file per resolution; `file_key` picks the npz array / CSV column / JSON
    key) or `errors="out/errors.json"` with solve_fn=None (precomputed
    errors per resolution; see io.load_errors)."""
    return convergence_check(
        solve_fn,
        resolutions,
        reference=reference,
        expected_order=expected_order,
        tol_order=tol_order,
        error_fn=error_fn,
        name=name,
        param_label="h",
        param_kind=param_kind,
        errors=errors,
        file_key=file_key,
        error_key=error_key,
        solution_scale=solution_scale,
        reference_key=reference_key,
    )


@record_config
def conservation_check(
    snapshots, conserved_fn, tol: float, name: str = "conservation"
) -> CheckResult:
    """`snapshots`: an iterable of field states over time/iterations.
    `conserved_fn(state) -> float`, e.g. total mass/momentum/energy on the
    mesh. Checks relative drift from the initial value stays within tol."""
    values = np.array([float(conserved_fn(s)) for s in snapshots], dtype=float)
    if values.size == 0:
        return CheckResult(
            name=f"conservation:{name}",
            status=Status.WARN.value,
            category="numerical",
            metric={},
            expected=f"<= {tol}",
            observed=None,
            evidence={},
            notes="No snapshots given.",
        )
    drift = float(np.max(np.abs(values - values[0])))
    rel_drift = drift / (abs(values[0]) or 1.0)
    status = threshold_status(rel_drift, tol)
    return CheckResult(
        name=f"conservation:{name}",
        status=status.value,
        category="numerical",
        metric={"max_relative_drift": rel_drift},
        expected=f"<= {tol}",
        observed=rel_drift,
        evidence={"initial": values[0], "final": values[-1], "n_snapshots": int(values.size)},
        notes="" if status == Status.PASS else f"Relative drift {rel_drift:.3g} exceeds tol={tol}.",
    )


@record_config
def manufactured_solution_check(
    solve_fn,
    exact_fn,
    resolutions,
    expected_order: float,
    tol_order: float = 0.3,
    error_fn=None,
    name: str = "manufactured_solution",
    param_kind: str = "auto",
    file_key=None,
    reference_key=None,
) -> CheckResult:
    """Method of Manufactured Solutions: `exact_fn(h)` returns the known
    exact solution sampled on the same grid solve_fn(h) would produce.
    Tagged model_validation (not plain 'numerical') because it validates
    against a known-correct answer, the same reasoning as
    ode.reference_solution_comparison."""
    result = convergence_check(
        solve_fn,
        resolutions,
        reference=exact_fn,
        expected_order=expected_order,
        tol_order=tol_order,
        error_fn=error_fn,
        name=name,
        param_label="h",
        param_kind=param_kind,
        file_key=file_key,
        reference_key=reference_key,
    )
    result.category = "model_validation"
    return result
