"""ODE checks: timestep convergence, invariant preservation, comparison
against a known analytic/reference solution."""

from __future__ import annotations

import numpy as np

from ._util import threshold_status
from .convergence import convergence_check
from .schema import CheckResult, Status


def timestep_convergence(
    solve_fn,
    dts,
    reference=None,
    expected_order: float | None = None,
    tol_order: float = 0.3,
    error_fn=None,
    name: str = "timestep_convergence",
) -> CheckResult:
    """solve_fn(dt) -> final state (or full trajectory) for that timestep.
    See convergence.convergence_check for the `reference`/error semantics."""
    return convergence_check(
        solve_fn,
        dts,
        reference=reference,
        expected_order=expected_order,
        tol_order=tol_order,
        error_fn=error_fn,
        name=name,
        param_label="dt",
    )


def invariant_preservation(
    trajectory, invariant_fn, tol: float, name: str = "invariant"
) -> CheckResult:
    """`trajectory`: an iterable of states. `invariant_fn(state) -> float`,
    e.g. total energy for a Hamiltonian system. Checks the invariant's
    relative drift from its initial value stays within tol over the run."""
    values = np.array([float(invariant_fn(s)) for s in trajectory], dtype=float)
    if values.size == 0:
        return CheckResult(
            name=f"invariant_preservation:{name}",
            status=Status.WARN.value,
            category="numerical",
            metric={},
            expected=f"<= {tol}",
            observed=None,
            evidence={},
            notes="Empty trajectory.",
        )
    drift = float(np.max(np.abs(values - values[0])))
    rel_drift = drift / (abs(values[0]) or 1.0)
    status = threshold_status(rel_drift, fail_threshold=tol * 10, warn_threshold=tol)
    return CheckResult(
        name=f"invariant_preservation:{name}",
        status=status.value,
        category="numerical",
        metric={"max_relative_drift": rel_drift},
        expected=f"<= {tol}",
        observed=rel_drift,
        evidence={"initial": values[0], "final": values[-1], "n_samples": int(values.size)},
    )


def reference_solution_comparison(
    numerical, analytic, tol: float, name: str = "reference_solution"
) -> CheckResult:
    """Direct comparison against a known analytic solution. This is
    model_validation, not plain numerical verification: it validates that
    the solver reproduces a *known-correct answer*, not just that it's
    internally consistent (e.g. convergent at the expected order)."""
    numerical = np.asarray(numerical, dtype=float)
    analytic = np.asarray(analytic, dtype=float)
    denom = np.linalg.norm(analytic) or 1.0
    err = float(np.linalg.norm(numerical - analytic) / denom)
    status = threshold_status(err, fail_threshold=tol * 10, warn_threshold=tol)
    return CheckResult(
        name=f"reference_solution:{name}",
        status=status.value,
        category="model_validation",
        metric={"relative_error": err},
        expected=f"<= {tol}",
        observed=err,
        evidence={},
    )
