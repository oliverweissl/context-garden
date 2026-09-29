"""trellis: an independent scientific correctness gate.

Deterministic checks across four tiers -- implementation, numerical,
model_validation, empirical -- and a Report that flags tiers never
exercised (see schema.build_report). Requires numpy (the only skill allowed
a non-stdlib dependency; hand-rolled eigen/cond code would be less
trustworthy).
"""

try:
    import numpy  # noqa: F401
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "trellis requires numpy (`pip install numpy`). This is the one dependency "
        "in this skill suite that isn't stdlib-only -- see SKILL.md for why."
    ) from e

from . import io, linalg, lock, ode, optimization, pde, stochastic, universal
from .schema import CATEGORIES, CheckResult, Report, Status, build_report

__all__ = [
    "io",
    "lock",
    "linalg",
    "ode",
    "optimization",
    "pde",
    "stochastic",
    "universal",
    "CATEGORIES",
    "CheckResult",
    "Report",
    "Status",
    "build_report",
]
