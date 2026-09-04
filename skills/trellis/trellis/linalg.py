"""Linear algebra checks: residuals, conditioning, reconstruction error,
symmetry/positive-definiteness."""
from __future__ import annotations

import numpy as np

from ._util import threshold_status
from .schema import CheckResult, Status


def residual_norm(A, x, b, tol: float, ord: int | str = 2, name: str = "linear_system") -> CheckResult:
    """||Ax - b|| against tol. WARN in (tol, 10*tol], FAIL above 10*tol."""
    A = np.asarray(A, dtype=float)
    x = np.asarray(x, dtype=float)
    b = np.asarray(b, dtype=float)
    r = A @ x - b
    norm = float(np.linalg.norm(r, ord=ord))
    status = threshold_status(norm, fail_threshold=tol * 10, warn_threshold=tol)
    return CheckResult(
        name=f"residual_norm:{name}", status=status.value, category="numerical",
        metric={"residual_norm": norm, "ord": str(ord)}, expected=f"<= {tol}", observed=norm,
        evidence={"residual_sample": r[:10]},
    )


def conditioning(A, warn_threshold: float = 1e8, fail_threshold: float = 1e12,
                  name: str = "matrix") -> CheckResult:
    A = np.asarray(A, dtype=float)
    cond = float(np.linalg.cond(A))
    if cond > fail_threshold:
        status = Status.FAIL
    elif cond > warn_threshold:
        status = Status.WARN
    else:
        status = Status.PASS
    return CheckResult(
        name=f"conditioning:{name}", status=status.value, category="numerical",
        metric={"condition_number": cond}, expected=f"<= {warn_threshold:.1e}", observed=cond, evidence={},
        notes="" if status == Status.PASS else
        "Ill-conditioned matrix -- small input perturbations can produce large solution errors; "
        "a passing residual check does not mean the solution is numerically trustworthy.",
    )


def reconstruction_error(original, reconstructed, tol: float, name: str = "reconstruction") -> CheckResult:
    """Relative Frobenius-norm error, e.g. for validating A ≈ U @ S @ Vt."""
    original = np.asarray(original, dtype=float)
    reconstructed = np.asarray(reconstructed, dtype=float)
    denom = np.linalg.norm(original) or 1.0
    err = float(np.linalg.norm(original - reconstructed) / denom)
    status = threshold_status(err, fail_threshold=tol * 10, warn_threshold=tol)
    return CheckResult(
        name=f"reconstruction_error:{name}", status=status.value, category="numerical",
        metric={"relative_error": err}, expected=f"<= {tol}", observed=err, evidence={},
    )


def symmetry_check(A, tol: float = 1e-10, name: str = "matrix") -> CheckResult:
    A = np.asarray(A, dtype=float)
    asym = float(np.max(np.abs(A - A.T))) if A.size else 0.0
    status = threshold_status(asym, fail_threshold=tol * 100, warn_threshold=tol)
    return CheckResult(
        name=f"symmetry:{name}", status=status.value, category="numerical",
        metric={"max_asymmetry": asym}, expected=f"<= {tol}", observed=asym, evidence={},
    )


def positive_definite_check(A, name: str = "matrix") -> CheckResult:
    """Symmetrizes A (real matrices are expected to be symmetric here;
    pass A already symmetrized if that's not a safe assumption) and checks
    all eigenvalues are strictly positive."""
    A = np.asarray(A, dtype=float)
    try:
        eigvals = np.linalg.eigvalsh((A + A.T) / 2)
    except np.linalg.LinAlgError:
        return CheckResult(
            name=f"positive_definite:{name}", status=Status.FAIL.value, category="numerical",
            metric={}, expected="all eigenvalues > 0", observed="eigendecomposition failed", evidence={},
        )
    min_eig = float(np.min(eigvals))
    status = Status.PASS if min_eig > 0 else Status.FAIL
    return CheckResult(
        name=f"positive_definite:{name}", status=status.value, category="numerical",
        metric={"min_eigenvalue": min_eig}, expected="> 0", observed=min_eig,
        evidence={"eigenvalues_sample": np.sort(eigvals)[:5]},
    )
