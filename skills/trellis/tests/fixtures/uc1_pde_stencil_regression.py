"""UC1: a finite-difference stencil bug that regresses a 2nd-order scheme
to 1st-order. Ordinary unit tests at a single fixed resolution would keep
passing (the answer is still "close enough" at that one resolution) --
this spec catches it via mesh convergence.

BUGGED = True reproduces the regression (this fixture's default);
BUGGED = False shows the healthy 2nd-order scheme for comparison.
"""
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import trellis as S

BUGGED = os.environ.get("SENTINEL_UC1_BUGGED", "1") == "1"

X0 = 0.7
TRUE_DERIV = np.cos(X0)


def solve(h):
    """Approximates d/dx sin(x) at X0 using a 3-point stencil."""
    if BUGGED:
        # bug: the stencil was meant to be central difference (2nd order)
        # but a copy-paste error made it forward difference (1st order)
        # while keeping the "central difference" *label* in the code.
        return np.array([(np.sin(X0 + h) - np.sin(X0)) / h])
    return np.array([(np.sin(X0 + h) - np.sin(X0 - h)) / (2 * h)])


RESULTS = [
    S.pde.mesh_convergence(
        solve, resolutions=[0.1, 0.05, 0.025, 0.0125, 0.00625],
        reference=lambda h: np.array([TRUE_DERIV]),
        expected_order=2.0, tol_order=0.3, name="stencil_convergence_order",
    ),
    S.universal.nan_inf_check(solve(0.01), name="stencil_output"),
]

REMAINING_RISKS = [
    "Convergence order was only checked at a single evaluation point (x0=0.7); "
    "a stencil bug that's order-preserving but wrong at boundaries would not be caught here.",
]
