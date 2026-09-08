"""Monte Carlo / statistics checks: seed replication, confidence
intervals, before/after comparison, distribution sanity.

None of this uses scipy -- confidence intervals use the normal
(large-sample) approximation to the standard error of the mean, and
before/after comparison uses a CI-overlap heuristic rather than a formal
hypothesis test. Both are clearly documented approximations, not
precision statistics; see references/modules.md for exactly what they do
and don't guarantee, and when to reach for scipy.stats / a proper
bootstrap instead.
"""

from __future__ import annotations

import numpy as np

from .schema import CheckResult, Status

_Z = {0.90: 1.6448536269514722, 0.95: 1.959963984540054, 0.99: 2.5758293035489004}


def confidence_interval_from_samples(values, confidence: float = 0.95) -> list[float]:
    values = np.asarray(values, dtype=float)
    n = values.size
    if n == 0:
        return [None, None]
    if n == 1:
        return [float(values[0]), float(values[0])]
    mean = float(np.mean(values))
    sem = float(np.std(values, ddof=1) / np.sqrt(n))
    z = _Z.get(confidence, _Z[0.95])
    return [mean - z * sem, mean + z * sem]


def seed_replication_check(
    fn, seeds: list, metric_fn=None, name: str = "seed_replication"
) -> CheckResult:
    """Runs fn(seed) for every seed and reports mean/std/95% CI of
    metric_fn(result) across them. This is the direct answer to "the
    agent reported an improvement based on one random seed": run this
    with >= 3 (ideally >= 5-10) seeds before trusting a single-run number.

    Also flags the specific bug where a "different seed" produces
    bit-identical output across all seeds -- usually means the seed
    argument isn't actually wired into the RNG at all."""
    metric_fn = metric_fn or (lambda r: float(r))
    values = np.array([metric_fn(fn(seed)) for seed in seeds], dtype=float)
    n = values.size
    mean = float(np.mean(values)) if n else None
    std = float(np.std(values, ddof=1)) if n > 1 else 0.0
    ci = confidence_interval_from_samples(values)

    if n < 3:
        status, notes = (
            Status.WARN,
            "Fewer than 3 seeds -- the confidence interval below is not well-estimated.",
        )
    elif n > 1 and std == 0.0:
        status = Status.WARN
        notes = (
            "All seeds produced bit-identical results -- either the computation is genuinely "
            "deterministic given these inputs, or the seed argument is not actually affecting "
            "the randomness used. Verify the seed is wired through before trusting this as 'replicated'."
        )
    else:
        status, notes = Status.PASS, ""

    return CheckResult(
        name=f"seed_replication:{name}",
        status=status.value,
        category="empirical",
        metric={"mean": mean, "std": std, "n_seeds": n, "ci_95": ci},
        expected=">= 3 seeds for a defensible estimate",
        observed={"n_seeds": n, "mean": mean, "ci_95": ci},
        evidence={"values": values, "seeds": list(seeds)},
        notes=notes,
    )


def confidence_interval_check(
    values, expected_range=None, confidence: float = 0.95, name: str = "confidence_interval"
) -> CheckResult:
    """Reports the CI; if `expected_range=(lo, hi)` is given, FAILs if the
    CI doesn't overlap it at all, WARNs if it overlaps but isn't fully
    contained, PASSes if fully contained."""
    values = np.asarray(values, dtype=float)
    ci = confidence_interval_from_samples(values, confidence)
    status = Status.PASS
    if expected_range is not None and ci[0] is not None:
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
    )


def compare_before_after(before, after, name: str = "before_after") -> CheckResult:
    """CI-overlap heuristic: PASS (difference plausibly real) if the 95%
    CIs of `before` and `after` don't overlap; WARN (not distinguishable
    from noise) if they do. This is a conservative approximation of a
    two-sample test, not a formal p-value -- non-overlapping CIs implies
    significance at roughly the equivalent of p<0.05 but overlapping CIs
    does NOT strictly imply non-significance (the true test is slightly
    more powerful than this heuristic). Good enough to catch "the entire
    claimed improvement is within one seed's worth of noise" (UC3);
    use scipy.stats for a real hypothesis test where the boundary matters."""
    before = np.asarray(before, dtype=float)
    after = np.asarray(after, dtype=float)
    ci_before = confidence_interval_from_samples(before)
    ci_after = confidence_interval_from_samples(after)
    overlap = not (ci_after[1] < ci_before[0] or ci_after[0] > ci_before[1])
    mean_before, mean_after = float(np.mean(before)), float(np.mean(after))

    if overlap:
        status = Status.WARN
        notes = (
            "95% CIs of before/after overlap -- the observed difference is not clearly "
            "distinguishable from sampling noise with this sample size. Do not claim an "
            "improvement from this alone; more replicates may resolve it."
        )
    else:
        status = Status.PASS
        notes = (
            "95% CIs do not overlap -- the difference is unlikely to be pure sampling noise "
            "(CI-overlap heuristic, not a formal hypothesis test; see this function's docstring)."
        )

    return CheckResult(
        name=f"compare_before_after:{name}",
        status=status.value,
        category="empirical",
        metric={
            "mean_before": mean_before,
            "mean_after": mean_after,
            "ci_before": ci_before,
            "ci_after": ci_after,
            "ci_overlap": overlap,
        },
        expected="non-overlapping 95% CIs to support a claimed difference",
        observed={"mean_before": mean_before, "mean_after": mean_after},
        evidence={"before": before, "after": after},
        notes=notes,
    )


def distribution_sanity_check(
    values,
    expected_mean: float | None = None,
    expected_std: float | None = None,
    tol: float = 0.2,
    name: str = "distribution",
) -> CheckResult:
    """Relative-tolerance check of sample mean/std against expected
    values, e.g. validating a sampler actually draws from the distribution
    it claims to."""
    values = np.asarray(values, dtype=float)
    mean = float(np.mean(values)) if values.size else None
    std = float(np.std(values, ddof=1)) if values.size > 1 else 0.0
    problems = []
    if expected_mean is not None and mean is not None:
        if abs(mean - expected_mean) > tol * (abs(expected_mean) or 1.0):
            problems.append(
                f"mean {mean:.4g} deviates from expected {expected_mean:.4g} by more than tol={tol}"
            )
    if expected_std is not None:
        if abs(std - expected_std) > tol * (abs(expected_std) or 1.0):
            problems.append(
                f"std {std:.4g} deviates from expected {expected_std:.4g} by more than tol={tol}"
            )
    status = Status.FAIL if problems else Status.PASS
    return CheckResult(
        name=f"distribution_sanity:{name}",
        status=status.value,
        category="empirical",
        metric={"mean": mean, "std": std, "n": int(values.size)},
        expected={"mean": expected_mean, "std": expected_std},
        observed={"mean": mean, "std": std},
        evidence={},
        notes="; ".join(problems),
    )
