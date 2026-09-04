"""PDE checks: mesh convergence / observed order, conservation, and a
manufactured-solution hook."""
from __future__ import annotations

import numpy as np

from ._util import threshold_status
from .convergence import convergence_check
from .schema import CheckResult, Status


def mesh_convergence(solve_fn, resolutions, reference=None, expected_order: float | None = None,
                      tol_order: float = 0.3, error_fn=None, name: str = "mesh_convergence") -> CheckResult:
    """solve_fn(h) -> field on a mesh of characteristic size h (or a
    resolution/cell-count -- whatever `resolutions` contains, as long as
    it's consistently interpretable as "smaller/coarser = worse"). This is
    the check for UC1: a stencil bug that regresses observed order from
    ~2 to ~1 shows up here even when ordinary tests at one fixed
    resolution keep passing."""
    return convergence_check(solve_fn, resolutions, reference=reference, expected_order=expected_order,
                              tol_order=tol_order, error_fn=error_fn, name=name, param_label="h")


def conservation_check(snapshots, conserved_fn, tol: float, name: str = "conservation") -> CheckResult:
    """`snapshots`: an iterable of field states over time/iterations.
    `conserved_fn(state) -> float`, e.g. total mass/momentum/energy on the
    mesh. Checks relative drift from the initial value stays within tol."""
    values = np.array([float(conserved_fn(s)) for s in snapshots], dtype=float)
    if values.size == 0:
        return CheckResult(
            name=f"conservation:{name}", status=Status.WARN.value, category="numerical",
            metric={}, expected=f"<= {tol}", observed=None, evidence={}, notes="No snapshots given.",
        )
    drift = float(np.max(np.abs(values - values[0])))
    rel_drift = drift / (abs(values[0]) or 1.0)
    status = threshold_status(rel_drift, fail_threshold=tol * 10, warn_threshold=tol)
    return CheckResult(
        name=f"conservation:{name}", status=status.value, category="numerical",
        metric={"max_relative_drift": rel_drift}, expected=f"<= {tol}", observed=rel_drift,
        evidence={"initial": values[0], "final": values[-1], "n_snapshots": int(values.size)},
    )


def manufactured_solution_check(solve_fn, exact_fn, resolutions, expected_order: float,
                                 tol_order: float = 0.3, error_fn=None,
                                 name: str = "manufactured_solution") -> CheckResult:
    """Method of Manufactured Solutions: `exact_fn(h)` returns the known
    exact solution sampled on the same grid solve_fn(h) would produce.
    Tagged model_validation (not plain 'numerical') because it validates
    against a known-correct answer, the same reasoning as
    ode.reference_solution_comparison."""
    result = convergence_check(solve_fn, resolutions, reference=exact_fn, expected_order=expected_order,
                                tol_order=tol_order, error_fn=error_fn, name=name, param_label="h")
    result.category = "model_validation"
    return result
