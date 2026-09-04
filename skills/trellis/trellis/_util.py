"""Internal helpers shared across check modules."""
from __future__ import annotations

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


def threshold_status(observed: float, fail_threshold: float, warn_threshold: float | None = None,
                      higher_is_worse: bool = True) -> Status:
    """Shared PASS/WARN/FAIL boundary logic: a value strictly worse than
    `fail_threshold` is FAIL, worse than `warn_threshold` (defaulting to
    half the distance to fail_threshold) is WARN, else PASS."""
    if warn_threshold is None:
        warn_threshold = fail_threshold / 2 if higher_is_worse else fail_threshold * 1.5
    if higher_is_worse:
        if observed > fail_threshold:
            return Status.FAIL
        if observed > warn_threshold:
            return Status.WARN
        return Status.PASS
    else:
        if observed < fail_threshold:
            return Status.FAIL
        if observed < warn_threshold:
            return Status.WARN
        return Status.PASS
