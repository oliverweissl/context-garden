"""Universal checks: applicable regardless of numerical domain."""

from __future__ import annotations

import os

import numpy as np

from ._util import record_config, threshold_status
from .schema import CheckResult, Status


@record_config
def nan_inf_check(data, name: str = "output", key=None) -> CheckResult:
    """FAIL if any NaN or Inf is present (no WARN tier). `data`
    may be a result-file path (see trellis.io.load; `key` selects)."""
    if isinstance(data, (str, os.PathLike)):
        from . import io as _io

        data = _io.load(data, key=key)
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


@record_config
def magnitude_check(
    value: float, expected_range: tuple[float, float], name: str = "value"
) -> CheckResult:
    """Plausibility check against an expected order-of-magnitude range:
    FAIL if outside [lo, hi] (no slack band -- widen the range explicitly
    if that is what you mean), else PASS. A units/scale sanity net, not a
    correctness proof."""
    lo, hi = expected_range
    v = float(value)
    status = Status.PASS if lo <= v <= hi else Status.FAIL
    return CheckResult(
        name=f"magnitude:{name}",
        status=status.value,
        category="numerical",
        metric={"value": v},
        expected=f"[{lo}, {hi}]",
        observed=v,
        evidence={},
        notes="" if status == Status.PASS else f"{v:.6g} lies outside the expected range [{lo}, {hi}].",
    )


@record_config
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

    def _diff(r):
        # NaN==NaN and inf==inf count as equal; any other NaN mismatch is inf
        same = (r == base) | (np.isnan(r) & np.isnan(base))
        d = np.where(same, 0.0, np.abs(r - base))
        return float(np.max(np.where(np.isnan(d), np.inf, d))) if base.size else 0.0

    max_diff = float(max(_diff(r) for r in runs[1:])) if len(runs) > 1 else 0.0
    matches = all(np.allclose(r, base, rtol=rtol, atol=atol, equal_nan=True) for r in runs[1:])
    n_nan = int(sum(np.isnan(r).sum() for r in runs))
    status = Status.PASS if matches else Status.FAIL
    notes = (
        ""
        if matches
        else "Non-deterministic output across identical calls -- check for uninitialized memory, unseeded RNG, "
        "or floating-point-summation order dependence (e.g. unordered parallel reduction)."
    )
    if n_nan:
        if status == Status.PASS:
            status = Status.WARN
        notes = (notes + " " if notes else "") + (
            f"Output contains NaN ({n_nan} value(s) across runs) -- reproducible NaN is still a numerical "
            "breakdown; run nan_inf_check."
        )
    return CheckResult(
        name=f"reproducibility:{name}",
        status=status.value,
        category="implementation",
        metric={"max_abs_diff": max_diff, "n_runs": n_runs, "n_nan": n_nan},
        expected=f"identical within rtol={rtol}, atol={atol} (NaN == NaN)",
        observed=f"max abs diff {max_diff:.3e}",
        evidence={},
        notes=notes,
    )


@record_config
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


@record_config
def threshold_check(
    value, tol: float, name: str = "value", key=None, category: str = "numerical", metric_name: str = "value"
) -> CheckResult:
    """Generic `value <= tol` gate for a scalar your solver already
    computed -- e.g. a residual or error written to JSON by a C++/Fortran/
    MPI job: threshold_check("out/result.json", 1e-8, key="residual").
    `value` may be a number or a result-file path (trellis.io.load with
    `key`). Strict: value > tol (or NaN) is FAIL."""
    source = None
    if isinstance(value, (str, os.PathLike)):
        from . import io as _io

        source = os.fspath(value)
        value = _io.load(value, key=key)
    arr = np.asarray(value, dtype=float)
    if arr.size != 1:
        raise ValueError(f"threshold_check:{name}: expected a scalar, got shape {arr.shape}")
    v = float(arr.reshape(()))
    status = threshold_status(v, tol)
    return CheckResult(
        name=f"threshold:{name}",
        status=status.value,
        category=category,
        metric={metric_name: v},
        expected=f"<= {tol}",
        observed=v,
        evidence={"source": source, "key": key},
        notes="" if status == Status.PASS else f"{metric_name} {v:.6g} exceeds tol={tol}.",
    )
