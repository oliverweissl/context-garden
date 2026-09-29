"""Shared convergence-order machinery for ode.py and pde.py: both are
"error shrinks as a discretization parameter (dt, h) shrinks, at some
expected polynomial order" checks, differing only in vocabulary. Catches
e.g. a stencil bug that drops a scheme from 2nd to 1st order while
per-test tolerances still pass.

Conventions (all internal math is done in terms of a step size h, where
smaller h = finer):
  - `param_kind="h"`: param values are step sizes (h, dt).
  - `param_kind="resolution"`: param values are cell/point counts N;
    solve_fn is still called with N, but the order is fitted against
    h = 1/N.
  - The default error metric is the grid-scaled (RMS) L2 norm,
    ||e||_2 / sqrt(size), which approximates the continuous L2 norm on a
    uniform grid. The unscaled Euclidean norm would bias the observed
    order down by d/2 in d dimensions.
"""

from __future__ import annotations

import os

import numpy as np

from ._util import array_fingerprint
from .schema import CheckResult, Status

_EPS = float(np.finfo(float).eps)


def rms_error(a, b) -> float:
    """Grid-scaled L2 (RMS) norm of a - b; complex-safe (uses |a - b|)."""
    a = np.asarray(a)
    b = np.asarray(b)
    if a.shape != b.shape and a.size != 1 and b.size != 1:
        raise ValueError(
            f"error_fn: output shape {a.shape} != reference shape {b.shape}; pass an `error_fn` "
            "that restricts/interpolates to a common grid."
        )
    d = np.abs(a - b)
    return float(np.sqrt(np.mean(d**2))) if d.size else 0.0


def observed_order(params, errors) -> float | None:
    """Least-squares fit of log(error) = order * log(h) + const, i.e.
    the standard way to read a convergence order off a refinement study.
    `params` must be step sizes h (smaller = finer). Returns None if fewer
    than 2 usable (positive param, positive error) points are available."""
    params = np.asarray(params, dtype=float)
    errors = np.asarray(errors, dtype=float)
    mask = (errors > 0) & (params > 0) & np.isfinite(errors)
    if int(mask.sum()) < 2:
        return None
    order, _const = np.polyfit(np.log(params[mask]), np.log(errors[mask]), 1)
    return float(order)


def local_orders(hs, errors) -> list:
    """Per-pair orders log(e_k/e_{k+1}) / log(h_k/h_{k+1}) (coarsest first).
    None where either error is non-positive/non-finite."""
    out = []
    for k in range(len(errors) - 1):
        e0, e1 = errors[k], errors[k + 1]
        if e0 > 0 and e1 > 0 and np.isfinite(e0) and np.isfinite(e1):
            out.append(float(np.log(e0 / e1) / np.log(hs[k] / hs[k + 1])))
        else:
            out.append(None)
    return out


def _to_h(values, param_kind: str):
    if param_kind == "h":
        return [float(v) for v in values]
    if param_kind == "resolution":
        return [1.0 / float(v) for v in values]
    raise ValueError(f"param_kind must be 'h' or 'resolution', got {param_kind!r}")


def resolve_param_kind(values, param_kind: str) -> str:
    """'auto': integer-valued params all >= 2 are read as resolutions
    (cell counts N, h = 1/N); anything else as step sizes h. Pass
    param_kind explicitly when that heuristic could be wrong."""
    if param_kind != "auto":
        return param_kind
    vals = [float(v) for v in values]
    if all(v >= 2 and float(v).is_integer() for v in vals):
        return "resolution"
    return "h"


def _as_source(obj, key, what):
    """A callable stays a callable; a str/PathLike becomes a file loader:
    a template containing '{' is formatted per param (from_files), a plain
    path is loaded once as a fixed array."""
    if obj is None or callable(obj) or not isinstance(obj, (str, os.PathLike)):
        return obj, None
    from . import io as _io

    src = os.fspath(obj)
    if "{" in src:
        return _io.from_files(src, key=key), src
    return _io.load(src, key=key), src


def convergence_check(
    solve_fn,
    param_values,
    reference=None,
    expected_order: float | None = None,
    tol_order: float = 0.3,
    error_fn=None,
    name: str = "convergence",
    param_label: str = "h",
    param_kind: str = "h",
    floor_rtol: float = 1e-8,
    errors=None,
    file_key=None,
    error_key=None,
    solution_scale: float | None = None,
    reference_key=None,
) -> CheckResult:
    """Runs solve_fn(param) for each value in param_values, computes an
    error per level, reads per-pair local orders, and compares the
    finest usable local order to `expected_order`.

    `param_kind`: "h" (step sizes; smaller = finer) or "resolution" (cell
    counts N; larger = finer, fitted against h = 1/N).

    `reference`:
      - None: self-referential (Richardson-style) -- uses successive
        differences d_k = err(u_k, u_{k+1}); with a constant refinement
        ratio r, log(d_k/d_{k+1})/log(r) is an unbiased order estimate.
        Needs >= 3 levels. If the levels' outputs have different shapes,
        pass an `error_fn(coarse, fine)` that restricts to common points.
      - a fixed array/value: compared against every run directly.
      - a callable(param) -> array: evaluated per param (e.g. an exact/
        manufactured solution sampled on that resolution's own grid).

    `error_fn(output, reference) -> float` defaults to the RMS
    (grid-scaled L2) norm of the difference.

    Guards: errors that stall at or below `floor_rtol` x solution scale
    (round-off / solver-tolerance floor) are trimmed and reported as WARN;
    local orders that haven't stabilised (pre-asymptotic) yield WARN
    rather than FAIL. A stabilised order outside expected_order +- tol_order
    is FAIL (no slack band); only an order *above* expected is WARN.

    Results from files (C++/Fortran/MPI/SLURM output): `solve_fn` and
    `reference` may be a path template such as "out/n{N}.npy" (formatted
    with N/n/h/dt/p = each param; `file_key` / `reference_key` select the
    npz array / CSV column / JSON key path), or a plain path (reference
    only: one fixed array). Alternatively pass `errors=` -- a {param: error} dict or a
    path to a JSON/CSV of precomputed errors per resolution (see
    io.load_errors; `error_key` selects the record field) -- with
    solve_fn=None; param_values may then be None (all params in the file).
    `solution_scale` sets the round-off-floor scale for precomputed errors.
    """
    config = {"tol_order": tol_order, "expected_order": expected_order, "floor_rtol": floor_rtol}
    solve_fn, solve_src = _as_source(solve_fn, file_key, "solve_fn")
    reference, ref_src = _as_source(reference, reference_key, "reference")
    if solve_src:
        config["solve_fn"] = solve_src
    precomputed = None
    if errors is not None:
        if isinstance(errors, (str, os.PathLike)):
            from . import io as _io

            config["errors"] = os.fspath(errors)
            precomputed = _io.load_errors(errors, key=error_key)
        else:
            precomputed = {float(k): float(v) for k, v in dict(errors).items()}
        if param_values is None:
            param_values = sorted(precomputed)
        missing = [p for p in param_values if float(p) not in precomputed]
        if missing:
            raise KeyError(f"{name}: no precomputed error for params {missing} (have {sorted(precomputed)})")
    elif solve_fn is None:
        raise ValueError(f"{name}: pass solve_fn (callable or file template) or errors=")
    param_kind = resolve_param_kind(param_values, param_kind)
    raw = sorted(set(float(p) for p in param_values), reverse=(param_kind == "h"))  # coarsest first
    hs = _to_h(raw, param_kind)
    config.update({"param_values": raw, "param_kind": param_kind})
    outputs = (
        None
        if precomputed is not None
        else [np.asarray(solve_fn(int(p) if param_kind == "resolution" else p)) for p in raw]
    )
    err_fn = error_fn or rms_error
    if param_kind == "resolution":
        param_label = "N"
    base_evidence = {param_label: raw, "param_kind": param_kind, "h_used_for_fit": hs}

    def _warn(notes, evidence=None):
        return CheckResult(
            name=name,
            status=Status.WARN.value,
            category="numerical",
            metric={"observed_order": None},
            expected=expected_order,
            observed=None,
            evidence=evidence or base_evidence,
            notes=notes,
            config=config,
        )

    extra_notes = []
    if precomputed is not None:
        errors = [precomputed[p] for p in raw]
        scale = float(solution_scale) if solution_scale else 0.0
        used_hs = list(hs)
        used_params = list(raw)
    elif reference is not None:
        scale = max((rms_error(o, 0.0) for o in outputs), default=0.0)
        if callable(reference):
            refs = [np.asarray(reference(int(p) if param_kind == "resolution" else p)) for p in raw]
        else:
            refs = [np.asarray(reference)] * len(raw)
        config["reference"] = array_fingerprint(refs)
        if ref_src:
            config["reference_source"] = ref_src
        scale = max([scale] + [rms_error(r, 0.0) for r in refs])
        errors = [float(err_fn(out, ref)) for out, ref in zip(outputs, refs)]
        used_hs = list(hs)
        used_params = list(raw)
    else:
        scale = max((rms_error(o, 0.0) for o in outputs), default=0.0)
        if len(outputs) < 3:
            return _warn(
                "Self-referential convergence needs >=3 resolutions (successive differences of 3 levels "
                "give one order estimate); pass an explicit `reference` to use only 2, or add another resolution."
            )
        if error_fn is None and len({o.shape for o in outputs}) > 1:
            return _warn(
                "Self-referential convergence: outputs have different shapes across levels "
                f"({[o.shape for o in outputs]}); pass an `error_fn(coarse, fine)` that restricts both "
                "to common grid points (or a `reference`)."
            )
        errors = [float(err_fn(outputs[k], outputs[k + 1])) for k in range(len(outputs) - 1)]
        used_hs = list(hs[:-1])
        used_params = list(raw[:-1])
        ratios = [hs[k] / hs[k + 1] for k in range(len(hs) - 1)]
        if max(ratios) > min(ratios) * 1.05:
            extra_notes.append(
                f"refinement ratios are not constant ({[round(r, 3) for r in ratios]}); "
                "self-referential order estimates are only exact for a constant ratio."
            )

    locs = local_orders(used_hs, errors)
    order_fit = observed_order(used_hs, errors)

    # round-off / tolerance floor: trim from the first level whose error is
    # (near) zero relative to the solution scale, or that stalls at a tiny level
    floor_abs = floor_rtol * (scale or 1.0)
    n_keep = len(errors)
    for k, e in enumerate(errors):
        stalled = k > 0 and locs[k - 1] is not None and locs[k - 1] < 0.5
        if not np.isfinite(e):
            n_keep = k
            break
        if e <= 100 * _EPS * (scale or 1.0) or (stalled and e <= floor_abs):
            n_keep = k
            break
    floor_hit = n_keep < len(errors)
    k_locs = [p for p in locs[: max(n_keep - 1, 0)] if p is not None]

    evidence = dict(base_evidence)
    evidence.update(
        {
            "used_" + param_label: used_params,
            "errors": errors,
            "local_orders": locs,
            "fit_order_all_points": order_fit,
            "error_scale": scale,
        }
    )
    if floor_hit:
        evidence["floor_trimmed_from_index"] = n_keep
        extra_notes.append(
            f"errors stop decreasing / reach the round-off or solver-tolerance floor from level {n_keep} "
            f"(error {errors[n_keep]:.3g} vs solution scale {scale:.3g}); those levels were excluded -- "
            "use coarser resolutions or tighter solver tolerances."
        )

    if not k_locs:
        return _warn(
            "Could not compute an order in the asymptotic range (need >=2 levels with positive error "
            "above the round-off floor). " + " ".join(extra_notes),
            evidence,
        )

    order = k_locs[-1]  # finest usable pair decides
    # asymptotic range: successive local orders agree, and their changes are
    # not growing under refinement (growing changes = still pre-asymptotic)
    deltas = [abs(k_locs[i + 1] - k_locs[i]) for i in range(len(k_locs) - 1)]
    stabilised = not deltas or (
        deltas[-1] <= max(0.1, tol_order / 2)
        and (len(deltas) < 2 or deltas[-1] <= max(deltas[-2], 0.02))
    )
    metric = {
        "observed_order": order,
        "local_orders": locs,
        "fit_order": order_fit,
        "finest_error": errors[max(n_keep, 1) - 1],
    }

    if expected_order is None:
        status = Status.WARN if floor_hit or not stabilised else Status.PASS
        notes = "No expected_order given -- reporting the observed order only, not judging it."
        if not stabilised:
            notes += " Local orders have not stabilised."
        return CheckResult(
            name=name,
            status=status.value,
            category="numerical",
            metric=metric,
            expected=None,
            observed=order,
            evidence=evidence,
            notes=" ".join([notes] + extra_notes),
            config=config,
        )

    diff = order - expected_order
    if abs(diff) <= tol_order and stabilised:
        status, notes = (Status.WARN if floor_hit else Status.PASS), ""
    elif not stabilised:
        status = Status.WARN
        notes = (
            f"Local orders {[round(p, 2) for p in k_locs]} have not stabilised (pre-asymptotic range?) "
            f"-- finest-pair order {order:.2f} vs expected {expected_order}. Add finer resolutions."
        )
    elif diff > 0:
        status = Status.WARN
        notes = (
            f"Observed order {order:.2f} is higher than expected {expected_order} -- superconvergence, "
            "a smooth/special test case, or a reference contaminated by the scheme itself; not verified."
        )
    elif floor_hit:
        status = Status.WARN
        notes = (
            f"Observed order {order:.2f} deviates from expected {expected_order}, but the finest levels hit "
            "a round-off/tolerance floor, which contaminates the finest pairs -- not verified; rerun on a "
            "range above the floor before concluding it is a scheme bug."
        )
    else:
        status = Status.FAIL
        notes = (
            f"Observed order {order:.2f} (finest pair; local orders {[round(p, 2) for p in k_locs]}) is a "
            f"significant deviation from expected {expected_order} -- likely a discretization/stencil bug "
            "(the class of regression single-resolution tests miss). Also rule out: an error norm not scaled "
            "by grid size, a reference not sampled on each level's grid, or wrong param_kind (h vs N)."
        )

    return CheckResult(
        name=name,
        status=status.value,
        category="numerical",
        metric=metric,
        expected=expected_order,
        observed=order,
        evidence=evidence,
        notes=" ".join(x for x in [notes] + extra_notes if x),
        config=config,
    )
