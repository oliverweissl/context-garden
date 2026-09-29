"""ODE checks: timestep convergence, invariant preservation, comparison
against a known analytic/reference solution."""

from __future__ import annotations

import os

import numpy as np

from ._util import array_fingerprint, record_config, threshold_status
from .convergence import convergence_check
from .schema import CheckResult, Status


@record_config
def timestep_convergence(
    solve_fn,
    dts=None,
    reference=None,
    expected_order: float | None = None,
    tol_order: float = 0.3,
    error_fn=None,
    name: str = "timestep_convergence",
    errors=None,
    file_key=None,
    error_key=None,
    solution_scale: float | None = None,
    reference_key=None,
) -> CheckResult:
    """solve_fn(dt) -> final state (or full trajectory) for that timestep.
    See convergence.convergence_check for the `reference`/error semantics
    and for file-based outputs (solve_fn="run_dt{dt:g}/u.npy", errors=...)."""
    return convergence_check(
        solve_fn,
        dts,
        reference=reference,
        expected_order=expected_order,
        tol_order=tol_order,
        error_fn=error_fn,
        name=name,
        param_label="dt",
        errors=errors,
        file_key=file_key,
        error_key=error_key,
        solution_scale=solution_scale,
        reference_key=reference_key,
    )


@record_config
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
    status = threshold_status(rel_drift, tol)
    return CheckResult(
        name=f"invariant_preservation:{name}",
        status=status.value,
        category="numerical",
        metric={"max_relative_drift": rel_drift},
        expected=f"<= {tol}",
        observed=rel_drift,
        evidence={"initial": values[0], "final": values[-1], "n_samples": int(values.size)},
        notes="" if status == Status.PASS else f"Relative invariant drift {rel_drift:.3g} exceeds tol={tol}.",
    )


@record_config
def reference_solution_comparison(
    numerical, analytic, tol: float, name: str = "reference_solution"
) -> CheckResult:
    """Direct comparison against a known analytic solution; tagged
    model_validation (not 'numerical') since it checks a known-correct
    answer, not just internal consistency. Either argument may be a result-file path (see trellis.io.load)."""
    from . import io as _io

    if isinstance(numerical, (str, os.PathLike)):
        numerical = _io.load(numerical)
    if isinstance(analytic, (str, os.PathLike)):
        analytic = _io.load(analytic)
    numerical = np.asarray(numerical, dtype=float)
    analytic = np.asarray(analytic, dtype=float)
    denom = np.linalg.norm(analytic) or 1.0
    err = float(np.linalg.norm(numerical - analytic) / denom)
    status = threshold_status(err, tol)
    return CheckResult(
        name=f"reference_solution:{name}",
        status=status.value,
        category="model_validation",
        metric={"relative_error": err},
        expected=f"<= {tol}",
        observed=err,
        evidence={},
        notes="" if status == Status.PASS else f"Relative error {err:.3g} vs reference exceeds tol={tol}.",
        config={"reference": array_fingerprint(analytic)},
    )
