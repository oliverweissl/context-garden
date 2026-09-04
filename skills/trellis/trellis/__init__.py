"""trellis: an independent scientific correctness gate.

Passing unit tests is evidence of software *behavior*. It is not
sufficient evidence of numerical/scientific *correctness*. This package
provides deterministic, executable checks (not recommendations) across
four tiers -- implementation, numerical, model_validation, empirical --
and a Report that explicitly flags which tiers were never exercised
(see schema.build_report), so "the tests passed" cannot be silently
read as "the science is right".

Requires numpy (the numerical domains this targets -- linear algebra,
ODEs/PDEs, Monte Carlo -- essentially always already depend on it in
practice; re-implementing eigenvalues/condition numbers by hand would
make the tool itself less numerically trustworthy, not more).
"""
try:
    import numpy  # noqa: F401
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "trellis requires numpy (`pip install numpy`). This is the one dependency "
        "in this skill suite that isn't stdlib-only -- see SKILL.md for why."
    ) from e

from . import linalg, ode, optimization, pde, stochastic, universal
from .schema import CATEGORIES, CheckResult, Report, Status, build_report

__all__ = [
    "linalg", "ode", "optimization", "pde", "stochastic", "universal",
    "CATEGORIES", "CheckResult", "Report", "Status", "build_report",
]
