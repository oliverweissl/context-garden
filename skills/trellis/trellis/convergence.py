"""Shared convergence-order machinery for ode.py and pde.py: both are
"error shrinks as a discretization parameter (dt, h) shrinks, at some
expected polynomial order" checks, differing only in vocabulary. This is
the module most directly responsible for catching UC1-style regressions
(a stencil bug that quietly drops a scheme from 2nd-order to 1st-order
while individual test tolerances still happen to pass).
"""

from __future__ import annotations

import numpy as np

from .schema import CheckResult, Status


def observed_order(params, errors) -> float | None:
    """Least-squares fit of log(error) = order * log(param) + const, i.e.
    the standard way to read a convergence order off a refinement study.
    Returns None if fewer than 2 usable (positive param, positive error)
    points are available."""
    params = np.asarray(params, dtype=float)
    errors = np.asarray(errors, dtype=float)
    mask = (errors > 0) & (params > 0)
    if int(mask.sum()) < 2:
        return None
    order, _const = np.polyfit(np.log(params[mask]), np.log(errors[mask]), 1)
    return float(order)


def convergence_check(
    solve_fn,
    param_values,
    reference=None,
    expected_order: float | None = None,
    tol_order: float = 0.3,
    error_fn=None,
    name: str = "convergence",
    param_label: str = "h",
) -> CheckResult:
    """Runs solve_fn(param) for each value in param_values, computes an
    error at each against `reference`, fits the observed order via
    log-log regression, and compares to `expected_order`.

    `reference`:
      - None: self-referential -- compares each coarser run's output to
        the finest-resolution run (the finest run is excluded from the
        fit, since it has ~0 error against itself by construction).
      - a fixed array/value: compared against every run directly.
      - a callable(param) -> array: evaluated per param (e.g. an exact/
        manufactured solution sampled on that resolution's own grid).

    `error_fn(output, reference) -> float` defaults to the L2 norm of the
    difference; override for domain-specific error metrics.
    """
    param_values = sorted(set(float(p) for p in param_values), reverse=True)  # coarsest first
    outputs = [np.asarray(solve_fn(p), dtype=float) for p in param_values]
    err_fn = error_fn or (lambda a, b: float(np.linalg.norm(np.asarray(a) - np.asarray(b))))

    if reference is not None:
        if callable(reference):
            refs = [np.asarray(reference(p), dtype=float) for p in param_values]
        else:
            ref_arr = np.asarray(reference, dtype=float)
            refs = [ref_arr] * len(param_values)
        errors = [err_fn(out, ref) for out, ref in zip(outputs, refs)]
        used_params = list(param_values)
    else:
        if len(outputs) < 3:
            return CheckResult(
                name=name,
                status=Status.WARN.value,
                category="numerical",
                metric={"observed_order": None},
                expected=expected_order,
                observed=None,
                evidence={param_label: param_values},
                notes="Self-referential convergence needs >=3 resolutions (2 to compare + 1 as reference); "
                "pass an explicit `reference` to use only 2, or add another resolution.",
            )
        finest = outputs[-1]
        errors = [err_fn(out, finest) for out in outputs[:-1]]
        used_params = list(param_values[:-1])

    order = observed_order(used_params, errors)
    evidence = {param_label: used_params, "errors": errors}

    if order is None:
        return CheckResult(
            name=name,
            status=Status.WARN.value,
            category="numerical",
            metric={"observed_order": None},
            expected=expected_order,
            observed=None,
            evidence=evidence,
            notes="Could not fit an observed order (need >=2 points with positive, nonzero error).",
        )

    if expected_order is None:
        return CheckResult(
            name=name,
            status=Status.PASS.value,
            category="numerical",
            metric={"observed_order": order},
            expected=None,
            observed=order,
            evidence=evidence,
            notes="No expected_order given -- reporting the observed order only, not judging it.",
        )

    diff = abs(order - expected_order)
    if diff <= tol_order:
        status, notes = Status.PASS, ""
    elif diff <= tol_order * 2:
        status = Status.WARN
        notes = (
            f"Observed order {order:.2f} deviates from expected {expected_order} by more than "
            f"tol_order={tol_order} but less than 2x that."
        )
    else:
        status = Status.FAIL
        notes = (
            f"Observed order {order:.2f} is a significant deviation from expected {expected_order} "
            "-- likely a discretization/stencil bug (this is exactly the class of regression "
            "that passing unit tests at a single resolution will not catch)."
        )

    return CheckResult(
        name=name,
        status=status.value,
        category="numerical",
        metric={"observed_order": order},
        expected=expected_order,
        observed=order,
        evidence=evidence,
        notes=notes,
    )
