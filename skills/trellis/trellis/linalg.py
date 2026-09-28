"""Linear algebra checks: residuals, conditioning, reconstruction error,
symmetry/positive-definiteness.

Dense numpy arrays need nothing beyond numpy. scipy.sparse matrices and
scipy.sparse.linalg.LinearOperator (or any object with `.matvec`, and
optionally `.rmatvec`) are also accepted and never densified: products
use A @ x / A.matvec, matrix norms come from scipy.sparse.linalg.norm
(sparse, ord 1/inf/fro) or estimates (onenormest; power iteration for the
2-norm), and condition numbers from a 1-norm estimate of A and of A^-1
(via a sparse LU). scipy is imported lazily -- only when such input is
passed -- and a clear ImportError is raised if it is unavailable.
"""

from __future__ import annotations

import math

import numpy as np

from ._util import record_config, threshold_status
from .schema import CheckResult, Status

_EPS = float(np.finfo(float).eps)


# --------------------------------------------------------------------------
# operator helpers (dense / scipy.sparse / LinearOperator)
# --------------------------------------------------------------------------


def _require_scipy(what: str):
    try:
        import scipy.sparse  # noqa: F401
        import scipy.sparse.linalg  # noqa: F401
    except ImportError as e:
        raise ImportError(
            f"trellis.linalg.{what}: sparse-matrix / LinearOperator input requires scipy "
            "(`pip install scipy`); dense numpy arrays work without it -- or pass np.asarray(A) "
            "if the matrix is small enough to densify."
        ) from e
    import scipy

    return scipy


def _kind(A) -> str:
    """'dense' | 'sparse' | 'linop'. Does not import scipy for numpy input."""
    if isinstance(A, np.ndarray):
        return "dense"
    mod = type(A).__module__ or ""
    if mod.startswith("scipy"):
        sc = _require_scipy("input")
        if sc.sparse.issparse(A):
            return "sparse"
        if isinstance(A, sc.sparse.linalg.LinearOperator):
            return "linop"
    if hasattr(A, "matvec"):
        return "linop"
    return "dense"


def _matvec(A, x, kind):
    if kind == "linop":
        x = np.asarray(x)
        if x.ndim == 1:
            return np.asarray(A.matvec(x)).reshape(-1)
        return np.column_stack([np.asarray(A.matvec(x[:, j])).reshape(-1) for j in range(x.shape[1])])
    return np.asarray(A @ x)


def _rmatvec(A, y, kind):
    """A^H y, or None if the operator has no adjoint."""
    if kind == "sparse":
        return np.asarray(A.conj().T @ y)
    try:
        return np.asarray(A.rmatvec(y)).reshape(-1)
    except (NotImplementedError, AttributeError, TypeError):
        return None


def _norm2_estimate(A, kind, iters: int = 100, rtol: float = 1e-6):
    """Power iteration on A^H A -> ||A||_2 (converges from below, so a
    relative residual computed with it is conservative). Without an
    adjoint: max ||A v|| / ||v|| over random probes (also a lower bound)."""
    n = A.shape[1]
    rng = np.random.default_rng(0)
    v = rng.standard_normal(n)
    v /= np.linalg.norm(v)
    if _rmatvec(A, _matvec(A, v, kind), kind) is None:
        best = 0.0
        for _ in range(20):
            w = rng.standard_normal(n)
            best = max(best, float(np.linalg.norm(_matvec(A, w, kind)) / np.linalg.norm(w)))
        return best, "random-probe lower bound (no rmatvec)"
    sigma = 0.0
    for _ in range(iters):
        w = _rmatvec(A, _matvec(A, v, kind), kind)
        nw = float(np.linalg.norm(w))
        if nw == 0.0:
            return 0.0, "power iteration"
        v = w / nw
        new = math.sqrt(nw)
        if abs(new - sigma) <= rtol * new:
            sigma = new
            break
        sigma = new
    return sigma, "power iteration (lower-bound estimate)"


def _matrix_norm(A, ord, kind):
    """(norm, method) without densifying sparse / operator input."""
    if kind == "dense":
        return float(np.linalg.norm(A, ord=ord)), "exact"
    if ord == 2:
        return _norm2_estimate(A, kind)  # needs only matvec/rmatvec, no scipy
    sc = _require_scipy("norm")
    if kind == "sparse" and ord in (1, np.inf, "fro"):
        return float(sc.sparse.linalg.norm(A, ord=ord)), "exact (scipy.sparse.linalg.norm)"
    op = sc.sparse.linalg.aslinearoperator(A)
    has_adj = _rmatvec(A, np.zeros(A.shape[0]), kind) is not None
    if ord in (1, np.inf) and has_adj:
        target = op if ord == 1 else op.H
        return float(sc.sparse.linalg.onenormest(target)), "onenormest estimate"
    est, method = _norm2_estimate(A, kind)
    return est, f"2-norm {method} (ord={ord} unavailable for this operator)"


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------


@record_config
def residual_norm(
    A,
    x,
    b,
    tol: float,
    ord: int | str = 2,
    name: str = "linear_system",
    relative: bool = True,
    cond: float | None = None,
) -> CheckResult:
    """Normwise relative residual (backward error)
    ||Ax - b|| / (||A||*||x|| + ||b||) against tol by default
    (`relative=False` judges the absolute ||Ax - b|| instead); both are
    reported. Strict: judged value > tol is FAIL (no slack band).

    An absolute residual is scale-dependent: x = 0 for a tiny b passes an
    absolute check while being 100% wrong. If `cond` (e.g. from
    `conditioning`) is given, the forward-error bound cond * backward_error
    is reported and a PASS is downgraded to WARN when it exceeds tol --
    a small residual does not imply an accurate x for an ill-conditioned A.
    Without `cond` a PASS only certifies the backward error, not the
    forward error in x.

    `A` may be a dense array, a scipy.sparse matrix or a LinearOperator
    (anything with .matvec); ||A|| is then exact (sparse ord 1/inf/fro) or
    an estimate recorded in metric['matrix_norm_method']. Estimates of
    ||A||_2 are lower bounds, which only makes the relative residual
    larger (conservative)."""
    kind = _kind(A)
    if kind == "dense":
        A = np.asarray(A)
    x = np.asarray(x)
    b = np.asarray(b)
    r = _matvec(A, x, kind) - b
    vec_ord = ord
    mat_ord = ord if ord in (1, 2, np.inf, "fro") else 2
    if vec_ord == "fro":
        vec_ord = 2
    norm = float(np.linalg.norm(r.ravel() if r.ndim > 1 and vec_ord != 2 else r, ord=vec_ord))
    a_norm, a_method = _matrix_norm(A, mat_ord, kind)
    denom = a_norm * float(np.linalg.norm(x, ord=vec_ord)) + float(np.linalg.norm(b, ord=vec_ord))
    rel = norm / denom if denom > 0 else (0.0 if norm == 0 else float("inf"))
    judged = rel if relative else norm
    status = threshold_status(judged, tol)
    metric = {
        "relative_residual": rel,
        "residual_norm": norm,
        "ord": str(ord),
        "matrix_kind": kind,
        "matrix_norm": a_norm,
        "matrix_norm_method": a_method,
    }
    notes = ""
    if status == Status.FAIL:
        notes = f"{'Relative' if relative else 'Absolute'} residual {judged:.3g} exceeds tol={tol}."
    if cond is not None:
        bound = float(cond) * rel
        metric["forward_error_bound"] = bound
        if bound > tol and status == Status.PASS:
            status = Status.WARN
            notes = (
                f"Residual is small, but cond(A)*backward_error = {bound:.3g} > tol={tol}: the solution x "
                "itself may still be inaccurate (ill-conditioned system)."
            )
    return CheckResult(
        name=f"residual_norm:{name}",
        status=status.value,
        category="numerical",
        metric=metric,
        expected=f"{'relative' if relative else 'absolute'} residual <= {tol}",
        observed=judged,
        evidence={"residual_sample": np.abs(np.asarray(r).ravel()[:10])},
        notes=notes,
        config={"cond_provided": cond is not None},
    )


def _cond_estimate(A, kind, inverse=None):
    """(cond, method, note). Sparse: onenormest(A) * onenormest(A^-1) with
    A^-1 applied through scipy.sparse.linalg.splu. LinearOperator: only if
    `inverse` (operator or callable solve(y)) is given."""
    sc = _require_scipy("conditioning")
    spla = sc.sparse.linalg
    n = A.shape[0]
    if kind == "sparse":
        a1 = float(spla.norm(A, 1))
        if inverse is None:
            try:
                lu = spla.splu(sc.sparse.csc_matrix(A))
            except RuntimeError as e:  # "Factor is exactly singular"
                return float("inf"), "sparse LU", f"LU factorization failed ({e}) -- matrix is singular."
            except MemoryError:
                return None, None, "sparse LU ran out of memory -- condition number not estimated."
            inv = spla.LinearOperator(
                A.shape, matvec=lu.solve, rmatvec=lambda y: lu.solve(y, trans="T"), dtype=float
            )
        else:
            inv = inverse
    else:
        if inverse is None:
            return None, None, (
                "LinearOperator input: cannot estimate cond(A) without densifying or an inverse -- "
                "pass inverse=<operator or solve callable> (e.g. a preconditioner-free direct solve)."
            )
        op = spla.aslinearoperator(A)
        if _rmatvec(A, np.zeros(n), kind) is None:
            return None, None, "LinearOperator has no rmatvec -- 1-norm estimate impossible; cond skipped."
        a1 = float(spla.onenormest(op))
        inv = inverse
    if callable(inv) and not hasattr(inv, "matvec"):
        solve = inv
        inv = spla.LinearOperator(A.shape, matvec=solve, dtype=float)
    try:
        ainv1 = float(spla.onenormest(spla.aslinearoperator(inv)))
    except (TypeError, NotImplementedError):
        return None, None, "inverse operator has no rmatvec -- 1-norm estimate of A^-1 impossible; cond skipped."
    return a1 * ainv1, "1-norm estimate (onenormest(A) * onenormest(A^-1))", ""


@record_config
def conditioning(
    A, warn_threshold: float = 1e8, fail_threshold: float = 1e12, name: str = "matrix", inverse=None
) -> CheckResult:
    """Condition number: dense -> np.linalg.cond (2-norm); scipy.sparse ->
    1-norm estimate via onenormest on A and on A^-1 (sparse LU), never
    densified; LinearOperator -> only with `inverse=` (else WARN: not
    estimated). Estimates are typically within a factor ~3 (lower bounds)."""
    kind = _kind(A)
    method = "exact 2-norm (np.linalg.cond)"
    note = ""
    if kind == "dense":
        cond = float(np.linalg.cond(np.asarray(A, dtype=float)))
    else:
        cond, method, note = _cond_estimate(A, kind, inverse)
    if cond is None:
        return CheckResult(
            name=f"conditioning:{name}",
            status=Status.WARN.value,
            category="numerical",
            metric={"condition_number": None, "matrix_kind": kind},
            expected=f"<= {warn_threshold:.1e}",
            observed=None,
            evidence={},
            notes="Condition number not estimated (not verified): " + note,
        )
    if cond > fail_threshold or math.isnan(cond):
        status = Status.FAIL
    elif cond > warn_threshold:
        status = Status.WARN
    else:
        status = Status.PASS
    notes = (
        ""
        if status == Status.PASS
        else "Ill-conditioned matrix -- small input perturbations can produce large solution errors; "
        "a passing residual check does not mean the solution is numerically trustworthy."
    )
    if note:
        notes = (notes + " " + note).strip()
    return CheckResult(
        name=f"conditioning:{name}",
        status=status.value,
        category="numerical",
        metric={"condition_number": cond, "matrix_kind": kind, "method": method},
        expected=f"<= {warn_threshold:.1e}",
        observed=cond,
        evidence={},
        notes=notes,
    )


@record_config
def reconstruction_error(original, reconstructed, tol: float, name: str = "reconstruction") -> CheckResult:
    """Relative Frobenius-norm error, e.g. for validating A ≈ U @ S @ Vt.
    Accepts scipy.sparse on either side (norm via scipy.sparse.linalg.norm)."""
    ko, kr = _kind(original), _kind(reconstructed)
    if "linop" in (ko, kr):
        raise TypeError("reconstruction_error: needs explicit matrices, not LinearOperators")
    if "sparse" in (ko, kr):
        sc = _require_scipy("reconstruction_error")
        d = original - reconstructed

        def _fro(M):
            return float(sc.sparse.linalg.norm(M, "fro")) if sc.sparse.issparse(M) else float(np.linalg.norm(np.asarray(M)))

        denom = _fro(original) or 1.0
        err = _fro(d) / denom
    else:
        original = np.asarray(original, dtype=float)
        reconstructed = np.asarray(reconstructed, dtype=float)
        denom = np.linalg.norm(original) or 1.0
        err = float(np.linalg.norm(original - reconstructed) / denom)
    status = threshold_status(err, tol)
    return CheckResult(
        name=f"reconstruction_error:{name}",
        status=status.value,
        category="numerical",
        metric={"relative_error": err},
        expected=f"<= {tol}",
        observed=err,
        evidence={},
        notes="" if status == Status.PASS else f"Relative reconstruction error {err:.3g} exceeds tol={tol}.",
    )


@record_config
def symmetry_check(A, tol: float = 1e-10, name: str = "matrix") -> CheckResult:
    """max |A - A^T| (absolute) against tol, strict. Sparse: computed on the
    sparse difference. LinearOperator: probe estimate
    max |u^T A v - v^T A u| / (||u|| ||v||) over random u, v (a lower bound)."""
    kind = _kind(A)
    method = "exact"
    if kind == "dense":
        A = np.asarray(A, dtype=float)
        asym = float(np.max(np.abs(A - A.T))) if A.size else 0.0
    elif kind == "sparse":
        d = abs(A - A.T)
        asym = float(d.max()) if d.nnz else 0.0
    else:
        rng = np.random.default_rng(0)
        n = A.shape[0]
        asym = 0.0
        for _ in range(10):
            u, v = rng.standard_normal(n), rng.standard_normal(n)
            asym = max(
                asym,
                abs(float(u @ _matvec(A, v, kind)) - float(v @ _matvec(A, u, kind)))
                / (np.linalg.norm(u) * np.linalg.norm(v)),
            )
        method = "random-probe lower bound"
    status = threshold_status(asym, tol)
    return CheckResult(
        name=f"symmetry:{name}",
        status=status.value,
        category="numerical",
        metric={"max_asymmetry": asym, "method": method},
        expected=f"<= {tol}",
        observed=asym,
        evidence={},
        notes="" if status == Status.PASS else f"Asymmetry {asym:.3g} exceeds tol={tol}.",
    )


@record_config
def positive_definite_check(A, name: str = "matrix") -> CheckResult:
    """Symmetrizes A (real matrices are expected to be symmetric here;
    pass A already symmetrized if that's not a safe assumption) and checks
    min eigenvalue > n * eps * max|eigenvalue| (numerically positive
    definite -- an eigenvalue below that is indistinguishable from 0 in
    floating point). Sparse / LinearOperator (assumed symmetric): extreme
    eigenvalues via scipy.sparse.linalg.eigsh, no densification; ARPACK
    non-convergence is WARN."""
    kind = _kind(A)
    if kind != "dense" and A.shape[0] > 64:
        sc = _require_scipy("positive_definite_check")
        spla = sc.sparse.linalg
        M = (A + A.T) * 0.5 if kind == "sparse" else spla.aslinearoperator(A)
        try:
            lo = float(spla.eigsh(M, k=1, which="SA", return_eigenvectors=False, maxiter=20 * A.shape[0])[0])
            hi = float(spla.eigsh(M, k=1, which="LM", return_eigenvectors=False, maxiter=20 * A.shape[0])[0])
        except spla.ArpackNoConvergence:
            return CheckResult(
                name=f"positive_definite:{name}",
                status=Status.WARN.value,
                category="numerical",
                metric={"matrix_kind": kind},
                expected="all eigenvalues > 0",
                observed="eigsh did not converge",
                evidence={},
                notes="ARPACK did not converge for the extreme eigenvalues -- not verified.",
            )
        eigvals = np.array([lo, hi])
        n = A.shape[0]
    else:
        if kind == "sparse":
            A = A.toarray()  # tiny (<= 64): densifying is harmless
        elif kind == "linop":
            A = np.column_stack([_matvec(A, e, kind) for e in np.eye(A.shape[1])])
        A = np.asarray(A, dtype=float)
        try:
            eigvals = np.linalg.eigvalsh((A + A.T) / 2)
        except np.linalg.LinAlgError:
            return CheckResult(
                name=f"positive_definite:{name}",
                status=Status.FAIL.value,
                category="numerical",
                metric={},
                expected="all eigenvalues > 0",
                observed="eigendecomposition failed",
                evidence={},
            )
        n = eigvals.size
    min_eig = float(np.min(eigvals))
    threshold = float(_EPS * n * np.max(np.abs(eigvals)))
    status = Status.PASS if min_eig > threshold else Status.FAIL
    return CheckResult(
        name=f"positive_definite:{name}",
        status=status.value,
        category="numerical",
        metric={"min_eigenvalue": min_eig, "matrix_kind": kind},
        expected=f"> {threshold:.3g} (n*eps*max|eig|)",
        observed=min_eig,
        evidence={"eigenvalues_sample": np.sort(eigvals)[:5]},
    )
