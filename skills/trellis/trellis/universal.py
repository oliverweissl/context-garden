"""Universal checks: applicable regardless of numerical domain."""

from __future__ import annotations

import numpy as np

from .schema import CheckResult, Status


def nan_inf_check(data, name: str = "output") -> CheckResult:
    """FAIL if any NaN or Inf is present -- there is no WARN tier here: a
    NaN/Inf is an unambiguous numerical breakdown (overflow, division by
    zero, an out-of-domain evaluation), not a matter of degree."""
    arr = np.asarray(data, dtype=float)
    n_nan = int(np.isnan(arr).sum())
    n_inf = int(np.isinf(arr).sum())
    bad = n_nan + n_inf
    status = Status.FAIL if bad > 0 else Status.PASS
    return CheckResult(
        name=f"nan_inf:{name}",
        status=status.value,
        category="numerical",
        metric={"n_nan": n_nan, "n_inf": n_inf, "size": int(arr.size)},
        expected="0 NaN/Inf values",
        observed=f"{n_nan} NaN, {n_inf} Inf out of {arr.size}",
        evidence={"shape": list(arr.shape)},
        notes=(
            ""
            if bad == 0
            else "NaN/Inf indicates a numerical breakdown -- FAIL regardless of what downstream tests report."
        ),
    )


def magnitude_check(
    value: float, expected_range: tuple[float, float], name: str = "value"
) -> CheckResult:
    """Plausibility check against an expected order-of-magnitude range.
    WARN if outside the range but within one range-width of it (probably
    a units/scale slip); FAIL if far outside (probably wrong entirely)."""
    lo, hi = expected_range
    v = float(value)
    if lo <= v <= hi:
        status = Status.PASS
    else:
        span = (hi - lo) if hi > lo else max(abs(hi), abs(lo), 1.0)
        if (lo - span) <= v <= (hi + span):
            status = Status.WARN
        else:
            status = Status.FAIL
    return CheckResult(
        name=f"magnitude:{name}",
        status=status.value,
        category="numerical",
        metric={"value": v},
        expected=f"[{lo}, {hi}]",
        observed=v,
        evidence={},
    )


def reproducibility_check(
    fn,
    args: tuple = (),
    kwargs: dict | None = None,
    n_runs: int = 3,
    rtol: float = 1e-10,
    atol: float = 1e-12,
    name: str = "fn",
) -> CheckResult:
    """Calls fn(*args, **kwargs) n_runs times and checks all outputs match
    within tolerance. Catches non-determinism masquerading as a fixed
    result -- uninitialized memory, unseeded RNG, thread/race dependence,
    iteration-order-dependent floating point summation, etc."""
    kwargs = kwargs or {}
    runs = [np.asarray(fn(*args, **kwargs), dtype=float) for _ in range(n_runs)]
    base = runs[0]
    for r in runs[1:]:
        if r.shape != base.shape:
            return CheckResult(
                name=f"reproducibility:{name}",
                status=Status.FAIL.value,
                category="implementation",
                metric={},
                expected="identical output shape across runs",
                observed=f"shapes {base.shape} vs {r.shape}",
                evidence={},
            )
    max_diff = (
        float(max((np.max(np.abs(r - base)) if base.size else 0.0) for r in runs[1:]))
        if len(runs) > 1
        else 0.0
    )
    matches = all(np.allclose(r, base, rtol=rtol, atol=atol) for r in runs[1:])
    status = Status.PASS if matches else Status.FAIL
    return CheckResult(
        name=f"reproducibility:{name}",
        status=status.value,
        category="implementation",
        metric={"max_abs_diff": max_diff, "n_runs": n_runs},
        expected=f"identical within rtol={rtol}, atol={atol}",
        observed=f"max abs diff {max_diff:.3e}",
        evidence={},
        notes=(
            ""
            if matches
            else "Non-deterministic output across identical calls -- check for uninitialized memory, unseeded RNG, "
            "or floating-point-summation order dependence (e.g. unordered parallel reduction)."
        ),
    )


def parameter_sanity_check(params: dict, constraints: dict, name: str = "params") -> CheckResult:
    """`constraints` maps a parameter name to a predicate(value) -> bool,
    e.g. {"dt": lambda v: v > 0, "cfl": lambda v: 0 < v <= 1}."""
    violations = {}
    for key, predicate in constraints.items():
        if key not in params:
            violations[key] = "missing"
            continue
        try:
            ok = bool(predicate(params[key]))
        except Exception as e:  # noqa: BLE001 -- constraint predicates are caller code
            violations[key] = f"constraint raised {e!r}"
            continue
        if not ok:
            violations[key] = f"value {params[key]!r} failed constraint"
    status = Status.FAIL if violations else Status.PASS
    return CheckResult(
        name=f"parameter_sanity:{name}",
        status=status.value,
        category="implementation",
        metric={"n_checked": len(constraints), "n_violations": len(violations)},
        expected="all parameter constraints satisfied",
        observed=violations if violations else "all satisfied",
        evidence={"params": params},
    )
