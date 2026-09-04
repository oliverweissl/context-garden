"""Optimization checks: constraint violations, gradient checks,
termination criteria, sensitivity to initialization."""
from __future__ import annotations

import numpy as np

from ._util import threshold_status
from .schema import CheckResult, Status


def constraint_violation_check(x, constraints: list, tol: float = 1e-6, name: str = "constraints") -> CheckResult:
    """`constraints`: list of g(x) -> float, feasible when g(x) <= 0."""
    x = np.asarray(x, dtype=float)
    violations = [float(g(x)) for g in constraints]
    max_violation = max([v for v in violations if v > 0], default=0.0)
    status = threshold_status(max_violation, fail_threshold=tol * 10, warn_threshold=tol)
    return CheckResult(
        name=f"constraint_violation:{name}", status=status.value, category="numerical",
        metric={"max_violation": max_violation, "n_constraints": len(constraints)},
        expected=f"<= {tol}", observed=max_violation, evidence={"violations": violations},
    )


def gradient_check(f, grad_f, x0, eps: float = 1e-6, rtol: float = 1e-3, name: str = "gradient") -> CheckResult:
    """Central-difference finite-difference gradient vs. the analytic
    gradient your optimizer actually uses. A wrong analytic gradient is
    one of the most common silent optimization bugs -- it can still
    "converge" to a wrong point without any exception or test failure."""
    x0 = np.asarray(x0, dtype=float)
    analytic = np.asarray(grad_f(x0), dtype=float)
    numeric = np.zeros_like(x0)
    for i in range(x0.size):
        dx = np.zeros_like(x0)
        dx[i] = eps
        numeric[i] = (f(x0 + dx) - f(x0 - dx)) / (2 * eps)
    diff = np.abs(analytic - numeric)
    denom = np.maximum(np.maximum(np.abs(analytic), np.abs(numeric)), 1e-12)
    rel_err = float(np.max(diff / denom))
    status = threshold_status(rel_err, fail_threshold=rtol * 10, warn_threshold=rtol)
    return CheckResult(
        name=f"gradient_check:{name}", status=status.value, category="numerical",
        metric={"max_relative_error": rel_err}, expected=f"<= {rtol}", observed=rel_err,
        evidence={"analytic": analytic, "numeric": numeric},
    )


def termination_check(info: dict, name: str = "termination") -> CheckResult:
    """`info`: the optimizer's own result/status dict. Looks for
    'converged' or 'success', and 'hit_max_iter' if present. FAIL if not
    converged; WARN if converged but only by hitting the iteration cap
    (which often means "gave up", not "found the optimum")."""
    converged = bool(info.get("converged", info.get("success", False)))
    hit_max_iter = bool(info.get("hit_max_iter", False))
    if not converged:
        status = Status.FAIL
    elif hit_max_iter:
        status = Status.WARN
    else:
        status = Status.PASS
    return CheckResult(
        name=f"termination:{name}", status=status.value, category="implementation",
        metric={"converged": converged, "hit_max_iter": hit_max_iter},
        expected="converged=True, hit_max_iter=False", observed=info, evidence={},
        notes="" if status == Status.PASS else
        ("Optimizer did not report convergence." if not converged else
         "Optimizer only 'converged' by exhausting its iteration budget -- verify this is actually the optimum."),
    )


def initialization_sensitivity_check(solve_fn, x0_list: list, tol_spread: float, metric_fn=None,
                                      name: str = "init_sensitivity") -> CheckResult:
    """Runs solve_fn(x0) from each starting point in x0_list and reports
    the spread in a scalar metric of the result (default: the objective
    value itself, if solve_fn returns one; pass metric_fn otherwise).
    Large spread across starts on a problem assumed convex/well-behaved
    is a strong hint of a non-convex landscape, a local-minima trap, or a
    bug that only manifests from certain initializations."""
    metric_fn = metric_fn or (lambda r: float(r))
    results = [solve_fn(x0) for x0 in x0_list]
    metrics = np.array([metric_fn(r) for r in results], dtype=float)
    spread = float(np.max(metrics) - np.min(metrics))
    status = threshold_status(spread, fail_threshold=tol_spread * 10, warn_threshold=tol_spread)
    return CheckResult(
        name=f"init_sensitivity:{name}", status=status.value, category="numerical",
        metric={"spread": spread, "n_starts": len(x0_list)}, expected=f"<= {tol_spread}",
        observed=spread, evidence={"metrics": metrics},
    )
