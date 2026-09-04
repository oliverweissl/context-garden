"""UC2: an agent "fixes" a failing solver test by relaxing the tolerance
instead of fixing the actual numerical bug. The relaxed check would pass;
trellis's residual_norm is called with the project's real scientific
tolerance (1e-6), which the "fix" never actually satisfies.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import trellis as S

A = np.array([[4.0, 1.0], [1.0, 3.0]])
B = np.array([1.0, 2.0])

# the solver has a real bug and returns a solution that doesn't actually
# satisfy Ax=b -- imagine this came from an iterative solver that exited
# early due to an off-by-one in its loop bound
X_FROM_BUGGY_SOLVER = np.array([0.1, 0.5])

SCIENTIFIC_TOLERANCE = 1e-6  # the project's actual accuracy contract, never relaxed here

RESULTS = [
    S.linalg.residual_norm(A, X_FROM_BUGGY_SOLVER, B, tol=SCIENTIFIC_TOLERANCE, name="solver_residual"),
    S.linalg.conditioning(A, name="system_matrix"),
]

UNSUPPORTED_CLAIMS = [
    "The calling PR description claims this solver 'now passes' -- it passes only against a "
    "relaxed test tolerance, not the project's scientific tolerance checked above.",
]
