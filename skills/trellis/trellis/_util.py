"""Internal helpers shared across check modules."""

from __future__ import annotations

import functools
import hashlib
import inspect
import math
import os

import numpy as np

from .schema import Status


def to_jsonable(x):
    """Recursively convert numpy scalars/arrays into plain JSON-serializable
    Python types, for CheckResult.evidence/metric/observed fields."""
    if isinstance(x, np.ndarray):
        return x.tolist()
    if isinstance(x, (np.floating,)):
        return float(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.bool_,)):
        return bool(x)
    if isinstance(x, dict):
        return {k: to_jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [to_jsonable(v) for v in x]
    return x


def threshold_status(observed: float, tol: float, higher_is_worse: bool = True) -> Status:
    """Shared strict PASS/FAIL boundary: `observed > tol` (or `< tol` with
    higher_is_worse=False) is FAIL, otherwise PASS; NaN is FAIL. No WARN
    band: WARN is reserved for *indeterminate* outcomes (pre-asymptotic
    orders, round-off floors, too few samples) and decided by individual
    checks, never by a slack factor on the tolerance."""
    observed = float(observed)
    if math.isnan(observed):
        return Status.FAIL
    if higher_is_worse:
        return Status.FAIL if observed > tol else Status.PASS
    return Status.FAIL if observed < tol else Status.PASS


# --------------------------------------------------------------------------
# fingerprints / hashes of reference data (for the spec lock)
# --------------------------------------------------------------------------

_FP_PROJECTIONS = 4


def file_sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def array_fingerprint(x) -> dict | None:
    """{"sha256": exact hash of dtype/shape/bytes, "shape": ..., "fingerprint":
    [norm, sum, max, min, 4 fixed random projections]}. The sha256 detects
    *any* change; the float fingerprint lets the lock treat last-ulp
    platform differences (a reference computed with sin() on another libm)
    as equal while any meaningful change to the reference data is not."""
    if x is None:
        return None
    if isinstance(x, (list, tuple)) and x and all(isinstance(v, np.ndarray) for v in x):
        parts = [array_fingerprint(v) for v in x]
        h = hashlib.sha256("|".join(p["sha256"] for p in parts).encode()).hexdigest()
        fp = [float(sum(p["fingerprint"][k] for p in parts)) for k in range(len(parts[0]["fingerprint"]))]
        return {"sha256": h, "shape": [p["shape"] for p in parts], "fingerprint": fp}
    a = np.asarray(x)
    if a.dtype == object:
        return {"sha256": hashlib.sha256(repr(x).encode()).hexdigest(), "shape": list(a.shape), "fingerprint": []}
    if np.iscomplexobj(a):
        a = np.stack([a.real, a.imag], axis=-1)
    a = np.ascontiguousarray(a, dtype=float)
    h = hashlib.sha256(str(a.shape).encode() + a.tobytes()).hexdigest()
    flat = np.where(np.isfinite(a), a, 0.0).ravel()
    if flat.size:
        w = np.random.default_rng(20240917).standard_normal((_FP_PROJECTIONS, flat.size))
        fp = [float(np.linalg.norm(flat)), float(flat.sum()), float(flat.max()), float(flat.min())]
        fp += [float(v) for v in w @ flat]
    else:
        fp = [0.0] * (4 + _FP_PROJECTIONS)
    return {"sha256": h, "shape": list(a.shape), "fingerprint": fp}


def fingerprints_equal(a: dict, b: dict, rtol: float = 1e-9) -> bool:
    if a.get("sha256") == b.get("sha256"):
        return True
    if a.get("shape") != b.get("shape"):
        return False
    fa, fb = a.get("fingerprint") or [], b.get("fingerprint") or []
    if not fa or len(fa) != len(fb):
        return False
    scale = max(max(abs(v) for v in fa), max(abs(v) for v in fb), 1e-300)
    return all(abs(p - q) <= rtol * scale for p, q in zip(fa, fb))


# --------------------------------------------------------------------------
# automatic strictness-config recording (what `trellis lock` pins)
# --------------------------------------------------------------------------

# arguments that are the *data under test*, not the spec's strictness --
# never recorded in the lock (they legitimately change every run)
_DATA_ARGS = {
    "A", "x", "b", "data", "values", "value", "snapshots", "trajectory", "numerical",
    "before", "after", "original", "reconstructed", "x0", "info", "params", "x0_list",
    "f", "grad_f", "fn", "args", "kwargs", "metric_fn", "conserved_fn", "invariant_fn",
    "constraints", "error_fn", "cond", "inverse", "name", "param_label", "analytic",
}
_SKIP = object()


def _config_value(v):
    if v is None or isinstance(v, (bool, str)):
        return v
    if isinstance(v, (int, np.integer)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        v = float(v)
        return v if math.isfinite(v) else repr(v)
    if isinstance(v, os.PathLike):
        return os.fspath(v)
    if callable(v):
        return _SKIP
    if isinstance(v, (list, tuple)) and all(
        isinstance(e, (bool, str, int, float, np.integer, np.floating)) or e is None for e in v
    ):
        return [_config_value(e) for e in v] if len(v) <= 10000 else array_fingerprint(np.asarray(v))
    if isinstance(v, dict):
        return _SKIP
    try:
        return array_fingerprint(v)
    except Exception:  # noqa: BLE001 -- unrecordable argument: just don't pin it
        return _SKIP


def record_config(fn):
    """Decorator for public check functions: after the check runs, record
    its strictness-relevant arguments (tolerances, expected orders, alpha,
    resolutions, seeds, reference fingerprints, ... -- including defaults)
    in `result.config`, so `trellis lock` / `trellis run` can detect a spec
    that was quietly loosened. Keys the check set itself take precedence."""
    sig = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        result = fn(*args, **kwargs)
        try:
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
        except TypeError:
            return result
        auto = {}
        for k, v in bound.arguments.items():
            if k in _DATA_ARGS:
                continue
            enc = _config_value(v)
            if enc is not _SKIP:
                auto[k] = enc
        result.config = to_jsonable({**auto, **(result.config or {})})
        return result

    return wrapper
