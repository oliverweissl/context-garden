"""Monte Carlo / statistics checks: seed replication, confidence
intervals, before/after comparison, distribution sanity. No scipy: all
distribution functions are implemented here. Supported confidence levels:
0.90, 0.95, 0.99.
"""

from __future__ import annotations

import math

import numpy as np

from ._util import array_fingerprint, record_config
from .schema import CheckResult, Status

_Z = {0.90: 1.6448536269514722, 0.95: 1.959963984540054, 0.99: 2.5758293035489004}

# two-sided Student-t critical values t_{(1+c)/2, df} for df = 1..30
_T_TABLE = {
    0.90: [6.314, 2.920, 2.353, 2.132, 2.015, 1.943, 1.895, 1.860, 1.833, 1.812,
           1.796, 1.782, 1.771, 1.761, 1.753, 1.746, 1.740, 1.734, 1.729, 1.725,
           1.721, 1.717, 1.714, 1.711, 1.708, 1.706, 1.703, 1.701, 1.699, 1.697],
    0.95: [12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228,
           2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101, 2.093, 2.086,
           2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042],
    0.99: [63.657, 9.925, 5.841, 4.604, 4.032, 3.707, 3.499, 3.355, 3.250, 3.169,
           3.106, 3.055, 3.012, 2.977, 2.947, 2.921, 2.898, 2.878, 2.861, 2.845,
           2.831, 2.819, 2.807, 2.797, 2.787, 2.779, 2.771, 2.763, 2.756, 2.750],
}


def t_critical(confidence: float, df: float) -> float:
    """Two-sided Student-t critical value. Non-integer df (Welch) is
    rounded down (conservative). Raises ValueError for unsupported
    confidence levels rather than silently substituting 95%."""
    if confidence not in _Z:
        raise ValueError(f"unsupported confidence {confidence}; use one of {sorted(_Z)}")
    df = max(1, int(math.floor(df)))
    if df <= 30:
        return _T_TABLE[confidence][df - 1]
    z = _Z[confidence]
    # Cornish-Fisher expansion of the t quantile around the normal quantile
    return (
        z
        + (z**3 + z) / (4 * df)
        + (5 * z**5 + 16 * z**3 + 3 * z) / (96 * df**2)
        + (3 * z**7 + 19 * z**5 + 17 * z**3 - 15 * z) / (384 * df**3)
    )


def confidence_interval_from_samples(values, confidence: float = 0.95) -> list[float]:
    """Student-t CI for the mean. [None, None] for n < 2 (a single sample
    carries no information about spread)."""
    values = np.asarray(values, dtype=float)
    n = values.size
    if n < 2:
        t_critical(confidence, 1)  # still validate `confidence`
        return [None, None]
    mean = float(np.mean(values))
    sem = float(np.std(values, ddof=1) / np.sqrt(n))
    t = t_critical(confidence, n - 1)
    return [mean - t * sem, mean + t * sem]


@record_config
def seed_replication_check(
    fn,
    seeds: list,
    metric_fn=None,
    expected: float | None = None,
    atol: float = 0.0,
    rtol: float = 0.0,
    confidence: float = 0.99,
    name: str = "seed_replication",
) -> CheckResult:
    """Runs fn(seed) for every seed and reports mean/std/t-CI of
    metric_fn(result) across them. Use >= 3 (ideally 5-10) seeds.

    `expected`: the true/reference value the estimator should hit. FAIL if
    it lies outside the t-CI (default 99%, so a correct estimator fails
    ~1% of the time) widened by atol + rtol*|expected| (a detectable bias). Without `expected` the result is at best WARN:
    replication only measures spread, not correctness.

    Any NaN/inf across seeds is FAIL. Also flags the specific bug where a
    "different seed" produces bit-identical output across all seeds --
    usually means the seed argument isn't actually wired into the RNG."""
    metric_fn = metric_fn or (lambda r: float(r))
    values = np.array([metric_fn(fn(seed)) for seed in seeds], dtype=float)
    n = values.size
    finite = np.isfinite(values)
    mean = float(np.mean(values)) if n else None
    std = float(np.std(values, ddof=1)) if n > 1 else 0.0
    ci = confidence_interval_from_samples(values, confidence) if finite.all() else [None, None]
    tol = atol + rtol * abs(expected) if expected is not None else 0.0

    if not finite.all():
        bad = [s for s, ok in zip(seeds, finite) if not ok]
        status, notes = Status.FAIL, f"Non-finite (NaN/inf) metric for seed(s) {bad}."
    elif n < 3:
        status, notes = (
            Status.WARN,
            "Fewer than 3 seeds -- the confidence interval below is not well-estimated.",
        )
    elif std == 0.0:
        status = Status.WARN
        notes = (
            "All seeds produced bit-identical results -- either the computation is genuinely "
            "deterministic given these inputs, or the seed argument is not actually affecting "
            "the randomness used. Verify the seed is wired through before trusting this as 'replicated'."
        )
    elif expected is None:
        status = Status.WARN
        notes = (
            "Replicated (spread only): no `expected` value given, so only seed-to-seed spread was measured, "
            "not correctness/bias. Pass expected=<true value> to verify the estimator."
        )
    elif ci[0] - tol <= expected <= ci[1] + tol:
        status, notes = Status.PASS, ""
    else:
        status = Status.FAIL
        notes = (
            f"Expected value {expected:.6g} lies outside the {confidence:.0%} CI "
            f"[{ci[0]:.6g}, {ci[1]:.6g}] (tolerance {tol:.3g}) -- the estimator is biased or wrong."
        )

    return CheckResult(
        name=f"seed_replication:{name}",
        status=status.value,
        category="empirical",
        metric={"mean": mean, "std": std, "n_seeds": n, "ci": ci, "confidence": confidence},
        expected=expected if expected is not None else ">= 3 seeds for a defensible estimate",
        observed={"n_seeds": n, "mean": mean, "ci": ci},
        evidence={"values": values, "seeds": list(seeds)},
        notes=notes,
    )


@record_config
def confidence_interval_check(
    values, expected_range=None, confidence: float = 0.95, name: str = "confidence_interval"
) -> CheckResult:
    """Reports the CI; if `expected_range=(lo, hi)` is given, FAILs if the
    CI doesn't overlap it at all, WARNs if it overlaps but isn't fully
    contained, PASSes if fully contained."""
    values = np.asarray(values, dtype=float)
    ci = confidence_interval_from_samples(values, confidence)
    status = Status.PASS
    notes = ""
    if ci[0] is None:
        status, notes = Status.WARN, "Fewer than 2 samples -- no confidence interval can be formed."
    elif expected_range is not None:
        lo, hi = expected_range
        fully_contained = lo <= ci[0] and ci[1] <= hi
        overlaps = ci[0] <= hi and ci[1] >= lo
        if fully_contained:
            status = Status.PASS
        elif overlaps:
            status = Status.WARN
        else:
            status = Status.FAIL
    return CheckResult(
        name=f"confidence_interval:{name}",
        status=status.value,
        category="empirical",
        metric={"ci": ci, "confidence": confidence, "n": int(values.size)},
        expected=expected_range,
        observed=ci,
        evidence={"values": values},
        notes=notes,
    )


@record_config
def compare_before_after(
    before,
    after,
    direction: str | None = None,
    confidence: float = 0.95,
    name: str = "before_after",
) -> CheckResult:
    """Welch two-sample t-test of mean(after) - mean(before).

    - n < 2 on either side: WARN (spread unknown; cannot distinguish from noise).
    - not significant at `confidence`: WARN.
    - significant, `direction=None`: PASS (a real difference, sign not judged).
    - significant, `direction="higher_is_better"` / `"lower_is_better"`:
      PASS if the change is an improvement, FAIL if it is a regression.
    """
    if direction not in (None, "higher_is_better", "lower_is_better"):
        raise ValueError("direction must be None, 'higher_is_better' or 'lower_is_better'")
    t_critical(confidence, 1)  # validate confidence up front
    before = np.asarray(before, dtype=float)
    after = np.asarray(after, dtype=float)
    nb, na = before.size, after.size
    mean_before = float(np.mean(before)) if nb else None
    mean_after = float(np.mean(after)) if na else None
    metric = {"mean_before": mean_before, "mean_after": mean_after, "n_before": nb, "n_after": na}
    delta = t_stat = df = t_crit = None
    significant = False

    if not (np.isfinite(before).all() and np.isfinite(after).all()):
        status, notes = Status.FAIL, "Non-finite (NaN/inf) values in before/after samples."
    elif nb < 2 or na < 2:
        status = Status.WARN
        notes = (
            "Need >= 2 samples per side to estimate noise -- a single run cannot be distinguished "
            "from sampling noise. Do not claim a difference from this."
        )
    else:
        delta = mean_after - mean_before
        vb, va = float(np.var(before, ddof=1)) / nb, float(np.var(after, ddof=1)) / na
        se = math.sqrt(vb + va)
        if se == 0.0:
            significant = delta != 0.0
            df = float(nb + na - 2)
        else:
            t_stat = delta / se
            df = (vb + va) ** 2 / (vb**2 / (nb - 1) + va**2 / (na - 1)) if vb + va > 0 else float(nb + na - 2)
            t_crit = t_critical(confidence, df)
            significant = abs(t_stat) > t_crit
        if not significant:
            status = Status.WARN
            notes = (
                f"Difference {delta:.4g} is not significant at {confidence:.0%} (Welch t-test) -- not "
                "distinguishable from sampling noise with this sample size. Do not claim a change from this alone."
            )
        elif direction is None:
            status = Status.PASS
            notes = f"Difference {delta:.4g} is significant at {confidence:.0%} (Welch t-test); sign not judged."
        else:
            improved = delta > 0 if direction == "higher_is_better" else delta < 0
            status = Status.PASS if improved else Status.FAIL
            notes = (
                f"Significant {'improvement' if improved else 'REGRESSION'} ({direction}): "
                f"mean {mean_before:.4g} -> {mean_after:.4g} (Welch t-test, {confidence:.0%})."
            )

    metric.update({"delta": delta, "t_stat": t_stat, "welch_df": df, "t_critical": t_crit, "significant": significant})
    return CheckResult(
        name=f"compare_before_after:{name}",
        status=status.value,
        category="empirical",
        metric=metric,
        expected=f"significant difference at {confidence:.0%}"
        + (f" in the {direction} direction" if direction else ""),
        observed={"mean_before": mean_before, "mean_after": mean_after},
        evidence={"before": before, "after": after},
        notes=notes,
    )


# --------------------------------------------------------------------------
# numpy/stdlib-only distribution functions (no scipy)
# --------------------------------------------------------------------------


def normal_cdf(x, mu: float = 0.0, sigma: float = 1.0):
    """Normal CDF (vectorised over math.erfc); usable as a KS `reference`."""
    z = (np.asarray(x, dtype=float) - mu) / (sigma * math.sqrt(2.0))
    out = 0.5 * np.vectorize(math.erfc, otypes=[float])(-z)
    return out if out.ndim else float(out)


def _norm_sf(z: float) -> float:
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def _norm_ppf(p: float) -> float:
    """Inverse normal CDF by bisection on erfc (|error| < 1e-12)."""
    lo, hi = -40.0, 40.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if 1.0 - _norm_sf(mid) < p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the regularized incomplete beta (modified Lentz)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 1000):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-15:
            break
    return h


def _betainc(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbt = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    if x < (a + 1.0) / (a + b + 2.0):
        return math.exp(lbt) * _betacf(a, b, x) / a
    return 1.0 - math.exp(lbt) * _betacf(b, a, 1.0 - x) / b


def t_two_sided_p(t: float, df: float) -> float:
    """Exact two-sided Student-t p-value via the regularized incomplete beta."""
    if not math.isfinite(t):
        return 0.0
    return _betainc(df / 2.0, 0.5, df / (df + t * t))


def chi2_two_sided_p_wh(x: float, k: float) -> float:
    """Two-sided chi-square(k) p-value via the Wilson-Hilferty cube-root
    normal approximation: (X/k)^(1/3) ~ N(1 - 2/(9k), 2/(9k))."""
    z = ((x / k) ** (1.0 / 3.0) - (1.0 - 2.0 / (9.0 * k))) / math.sqrt(2.0 / (9.0 * k))
    return min(1.0, 2.0 * _norm_sf(abs(z)))


def chi2_quantile_wh(q: float, k: float) -> float:
    """Wilson-Hilferty chi-square(k) quantile."""
    z = _norm_ppf(q)
    c = 2.0 / (9.0 * k)
    return k * max(1.0 - c + z * math.sqrt(c), 0.0) ** 3


def kolmogorov_sf(lam: float) -> float:
    """Asymptotic Kolmogorov survival function Q(lambda)."""
    if lam < 0.2:
        return 1.0
    total = sum((-1) ** (k - 1) * math.exp(-2.0 * k * k * lam * lam) for k in range(1, 101))
    return min(1.0, max(0.0, 2.0 * total))


def ks_test(values, reference):
    """KS statistic D and asymptotic p-value. `reference` is a CDF callable
    (one-sample test) or a reference sample (two-sample test). The
    p-value uses the Kolmogorov distribution with Stephens' small-sample
    correction lambda = (sqrt(ne) + 0.12 + 0.11/sqrt(ne)) * D; assumes
    continuous distributions (ties make it conservative)."""
    x = np.sort(np.asarray(values, dtype=float).ravel())
    n = x.size
    if callable(reference):
        try:
            cdf = np.asarray(reference(x), dtype=float)
            if cdf.shape != x.shape:
                raise ValueError
        except Exception:  # noqa: BLE001 -- scalar-only CDF: evaluate pointwise
            cdf = np.array([float(reference(v)) for v in x])
        i = np.arange(1, n + 1)
        d = float(max(np.max(i / n - cdf), np.max(cdf - (i - 1) / n)))
        ne, kind = float(n), "one-sample (reference CDF)"
    else:
        y = np.sort(np.asarray(reference, dtype=float).ravel())
        m = y.size
        grid = np.concatenate([x, y])
        d = float(np.max(np.abs(np.searchsorted(x, grid, side="right") / n - np.searchsorted(y, grid, side="right") / m)))
        ne, kind = n * m / (n + m), "two-sample (reference sample)"
    lam = (math.sqrt(ne) + 0.12 + 0.11 / math.sqrt(ne)) * d
    return d, kolmogorov_sf(lam), kind


_CDF_PROBES = np.concatenate([np.linspace(-10, 10, 81), [-1e3, -1e2, -30, 30, 1e2, 1e3]])


@record_config
def distribution_sanity_check(
    values,
    expected_mean: float | None = None,
    expected_std: float | None = None,
    reference=None,
    alpha: float = 0.01,
    assume_normal: bool = False,
    name: str = "distribution",
) -> CheckResult:
    """Hypothesis tests that `values` come from the claimed distribution,
    e.g. validating a sampler. All numpy/stdlib-only:

    - mean (`expected_mean`): one-sample t-test, t = (mean - mu) / (s/sqrt(n)).
    - variance (`expected_std`): chi-square test on (n-1) s^2 / sigma^2 with
      Wilson-Hilferty p-values. By default the degrees of freedom are
      kurtosis-adjusted (Var(s^2/sigma^2) ~ 2/(n-1) + excess_kurtosis/n,
      k_eff = 2/Var) so non-normal samplers are not falsely rejected; set
      assume_normal=True for the classical normal-theory test (k = n-1).
    - shape (`reference`): KS test vs a CDF callable (one-sample) or vs a
      reference sample (two-sample), asymptotic p-value.

    Bonferroni over the tests actually performed: each at alpha/m. FAIL if
    any p < alpha/m, PASS otherwise (a correct sampler FAILs with
    probability <= alpha, default 1%). WARN if n < 2 or nothing was tested."""
    values = np.asarray(values, dtype=float).ravel()
    n = int(values.size)
    finite = bool(np.all(np.isfinite(values)))
    mean = float(np.mean(values)) if n else None
    std = float(np.std(values, ddof=1)) if n > 1 else 0.0
    config = {"n_samples": n}
    if reference is not None:
        config["reference"] = (
            array_fingerprint(np.asarray(reference(_CDF_PROBES), dtype=float))
            if callable(reference)
            else array_fingerprint(np.sort(np.asarray(reference, dtype=float).ravel()))
        )
    planned = [t for t, v in (("mean", expected_mean), ("variance", expected_std), ("ks", reference)) if v is not None]
    tests = {}
    metric = {"mean": mean, "std": std, "n": n, "alpha": alpha}

    def _result(status, notes):
        metric["tests"] = tests
        return CheckResult(
            name=f"distribution_sanity:{name}",
            status=status.value,
            category="empirical",
            metric=metric,
            expected={"mean": expected_mean, "std": expected_std, "reference": reference is not None, "alpha": alpha},
            observed={"mean": mean, "std": std, "p_values": {k: v["p"] for k, v in tests.items()}},
            evidence={},
            notes=notes,
            config=config,
        )

    if not finite:
        return _result(Status.FAIL, "Non-finite (NaN/inf) samples.")
    if n < 2:
        return _result(Status.WARN, "Fewer than 2 samples -- no test possible.")
    if not planned:
        return _result(Status.WARN, "Nothing tested: pass expected_mean, expected_std and/or reference.")
    a_each = alpha / len(planned)
    if expected_mean is not None:
        se = std / math.sqrt(n)
        if se == 0.0:
            p = 1.0 if mean == expected_mean else 0.0
            t_stat = 0.0 if p == 1.0 else math.inf
        else:
            t_stat = (mean - expected_mean) / se
            p = t_two_sided_p(t_stat, n - 1)
        tests["mean"] = {"t": t_stat, "se": se, "df": n - 1, "p": p}
    if expected_std is not None:
        ratio = std**2 / float(expected_std) ** 2 if expected_std > 0 else math.inf
        dev = values - mean
        m2 = float(np.mean(dev**2))
        kurt = float(np.mean(dev**4) / m2**2 - 3.0) if m2 > 0 else 0.0
        if assume_normal:
            k = float(n - 1)
        else:
            var_ratio = 2.0 / (n - 1) + max(kurt, -2.0 + 1e-9) / n
            k = max(2.0 / max(var_ratio, 1e-300), 1.0)
        stat = k * ratio
        p = chi2_two_sided_p_wh(stat, k) if math.isfinite(stat) else 0.0
        tests["variance"] = {
            "chi2": stat,
            "df": k,
            "excess_kurtosis": kurt,
            "p": p,
            "accept_region": [chi2_quantile_wh(a_each / 2, k), chi2_quantile_wh(1 - a_each / 2, k)],
        }
    if reference is not None:
        d, p, kind = ks_test(values, reference)
        tests["ks"] = {"D": d, "p": p, "kind": kind}
    metric.update({"n_tests": len(tests), "alpha_per_test": a_each})
    rejected = [k for k, v in tests.items() if v["p"] < a_each]
    if not rejected:
        return _result(Status.PASS, "")
    parts = []
    for k in rejected:
        v = tests[k]
        if k == "mean":
            parts.append(f"mean {mean:.6g} != {expected_mean:.6g} (t={v['t']:.3g}, p={v['p']:.2g})")
        elif k == "variance":
            parts.append(f"std {std:.6g} != {expected_std:.6g} (chi2 p={v['p']:.2g})")
        else:
            parts.append(f"KS {v['kind']} D={v['D']:.3g}, p={v['p']:.2g}")
    return _result(
        Status.FAIL,
        f"Rejected at alpha={alpha} (Bonferroni over {len(tests)} test(s), {a_each:.3g} each): " + "; ".join(parts),
    )
