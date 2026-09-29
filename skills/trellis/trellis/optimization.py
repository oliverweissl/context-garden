"""Optimization checks: constraint violations, gradient checks,
termination criteria, sensitivity to initialization."""

from __future__ import annotations

import numpy as np

from ._util import record_config, threshold_status
from .schema import CheckResult, Status


@record_config
def constraint_violation_check(
    x, constraints: list, tol: float = 1e-6, name: str = "constraints"
) -> CheckResult:
    """`constraints`: list of g(x) -> float, feasible when g(x) <= 0."""
    x = np.asarray(x, dtype=float)
    violations = [float(g(x)) for g in constraints]
    max_violation = max([v for v in violations if v > 0], default=0.0)
    status = threshold_status(max_violation, tol)
    return CheckResult(
        name=f"constraint_violation:{name}",
        status=status.value,
        category="numerical",
        metric={"max_violation": max_violation, "n_constraints": len(constraints)},
        expected=f"<= {tol}",
        observed=max_violation,
        evidence={"violations": violations},
        notes="" if status == Status.PASS else f"Max constraint violation {max_violation:.3g} exceeds tol={tol}.",
    )


@record_config
def gradient_check(
    f,
    grad_f,
    x0,
    eps: float | None = None,
    rtol: float = 1e-3,
    f_rtol: float | None = None,
    fd_safety: float = 10.0,
    name: str = "gradient",
) -> CheckResult:
    """Central-difference finite-difference gradient vs. the analytic
    gradient your optimizer actually uses.

    Per coordinate i with step h_i = eps * max(1, |x_i|) (default eps =
    eps_mach**(1/3), the step minimising truncation + round-off for a
    central difference), the FD derivative D(h) carries an error estimate

        fd_err_i = |D(2h) - D(h)| / 3            (truncation, Richardson: D(h) = g + c h^2 + ...)
                 + f_rtol * (|f(x+h)| + |f(x-h)|) / (2h)   (round-off in f, f_rtol default 100*eps_mach)

    and the relative error is judged against a scale-aware denominator

        rel_i = |g_an - g_fd| / max(|g_an|, |g_fd|, fd_safety * fd_err_i / rtol)

    i.e. PASS iff |g_an - g_fd| <= max(rtol * |g|, fd_safety * fd_err_i):
    the plain relative test away from stationary points, the FD method's
    own accuracy near them (g ~ 0 or |f| >> |g| h), so no false FAIL.
    Coordinates judged on that floor are listed in evidence.
    Raise `f_rtol` for noisy objectives (e.g. an iterative inner solve)."""
    x0 = np.asarray(x0, dtype=float)
    u = float(np.finfo(float).eps)
    rel_step = float(eps) if eps is not None else u ** (1.0 / 3.0)
    f_rtol = float(f_rtol) if f_rtol is not None else 100.0 * u
    analytic = np.asarray(grad_f(x0), dtype=float).reshape(x0.shape)
    flat = x0.ravel()
    numeric = np.zeros(flat.size)
    fd_err = np.zeros(flat.size)
    steps = np.zeros(flat.size)

    def _f(xf):
        return float(f(xf.reshape(x0.shape)))

    for i in range(flat.size):
        h = rel_step * max(1.0, abs(flat[i]))
        h = (flat[i] + h) - flat[i]  # exactly representable step
        e = np.zeros_like(flat)
        e[i] = 1.0
        fp, fm = _f(flat + h * e), _f(flat - h * e)
        f2p, f2m = _f(flat + 2 * h * e), _f(flat - 2 * h * e)
        d1 = (fp - fm) / (2 * h)
        d2 = (f2p - f2m) / (4 * h)
        numeric[i] = d1
        fd_err[i] = abs(d2 - d1) / 3.0 + f_rtol * (abs(fp) + abs(fm)) / (2 * h)
        steps[i] = h
    numeric = numeric.reshape(x0.shape)
    fd_err = fd_err.reshape(x0.shape)
    diff = np.abs(analytic - numeric)
    mag = np.maximum(np.abs(analytic), np.abs(numeric))
    floor = fd_safety * fd_err / rtol
    denom = np.maximum(np.maximum(mag, floor), np.finfo(float).tiny)
    rel = diff / denom
    rel_err = float(np.max(rel)) if rel.size else 0.0
    if not np.all(np.isfinite(analytic)) or not np.all(np.isfinite(numeric)):
        rel_err = float("nan")
    status = threshold_status(rel_err, rtol)
    floor_limited = [int(i) for i in np.flatnonzero((floor > mag).ravel())]
    notes = ""
    if status == Status.FAIL:
        worst = int(np.nanargmax(rel.ravel())) if np.isfinite(rel_err) else -1
        notes = (
            f"Analytic gradient disagrees with the finite-difference gradient beyond rtol={rtol} "
            f"(worst coordinate {worst}: analytic {analytic.ravel()[worst]:.6g} vs FD {numeric.ravel()[worst]:.6g}, "
            f"FD error estimate {fd_err.ravel()[worst]:.2g})."
            if worst >= 0
            else "Non-finite analytic or finite-difference gradient."
        )
    elif floor_limited:
        notes = (
            f"{len(floor_limited)} coordinate(s) have |g| below what central differences resolve to rtol "
            "(near-stationary or cancellation-dominated); those are verified only to the FD accuracy "
            "fd_safety*fd_err (absolute), not to relative rtol."
        )
    return CheckResult(
        name=f"gradient_check:{name}",
        status=status.value,
        category="numerical",
        metric={"max_relative_error": rel_err, "max_abs_error": float(np.max(diff)) if diff.size else 0.0},
        expected=f"|g_an - g_fd| <= max(rtol*|g|, fd_safety*fd_err), rtol={rtol}",
        observed=rel_err,
        evidence={
            "analytic": analytic,
            "numeric": numeric,
            "fd_error_estimate": fd_err,
            "steps": steps,
            "floor_limited_coordinates": floor_limited,
        },
        notes=notes,
        config={"eps": rel_step, "f_rtol": f_rtol},
    )


@record_config
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
        name=f"termination:{name}",
        status=status.value,
        category="implementation",
        metric={"converged": converged, "hit_max_iter": hit_max_iter},
        expected="converged=True, hit_max_iter=False",
        observed=info,
        evidence={},
        notes=(
            ""
            if status == Status.PASS
            else (
                "Optimizer did not report convergence."
                if not converged
                else "Optimizer only 'converged' by exhausting its iteration budget -- verify this is actually the optimum."
            )
        ),
    )


@record_config
def initialization_sensitivity_check(
    solve_fn, x0_list: list, tol_spread: float, metric_fn=None, name: str = "init_sensitivity"
) -> CheckResult:
    """Runs solve_fn(x0) from each starting point in x0_list and reports
    the spread in a scalar metric of the result (default: the objective
    value itself, if solve_fn returns one; pass metric_fn otherwise).
    Large spread on a supposedly convex problem suggests local minima or an
    initialization-dependent bug."""
    metric_fn = metric_fn or (lambda r: float(r))
    results = [solve_fn(x0) for x0 in x0_list]
    metrics = np.array([metric_fn(r) for r in results], dtype=float)
    spread = float(np.max(metrics) - np.min(metrics))
    status = threshold_status(spread, tol_spread)
    return CheckResult(
        name=f"init_sensitivity:{name}",
        status=status.value,
        category="numerical",
        metric={"spread": spread, "n_starts": len(x0_list)},
        expected=f"<= {tol_spread}",
        observed=spread,
        evidence={"metrics": metrics},
        notes="" if status == Status.PASS else f"Spread {spread:.3g} across starts exceeds tol_spread={tol_spread}.",
    )
