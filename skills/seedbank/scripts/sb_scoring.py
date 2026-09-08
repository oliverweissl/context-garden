"""Deterministic promotion scoring.

value = (reuse_probability * avg_retrieval_cost + avg_failure_cost)
        * stability / persistent_token_cost

All quantities are heuristic estimates (see references/scoring.md for the
reasoning behind each term) -- there is no ground truth "correct" value,
only a consistent, explainable ranking.
"""

from __future__ import annotations

PLACEHOLDER_REPRESENTATION_TOKENS = 20  # used only to rank un-promoted candidates


def reuse_probability(access_count: int) -> float:
    """Laplace-smoothed frequency ratio: approaches 1.0 as access_count grows,
    starts modestly even at access_count=1 rather than 0 or 1 outright."""
    return access_count / (access_count + 2)


def stability(hash_changes: int) -> float:
    """1.0 if the backing source(s) never changed across observations (or
    there are no sources to hash, e.g. a stated policy/invariant); decays
    as the source content churns underneath repeated observations."""
    return 1.0 if hash_changes <= 0 else 1.0 / (1.0 + hash_changes)


def compute_value(stats: dict, persistent_token_cost: int) -> float:
    n = max(1, stats.get("access_count", 1))
    avg_retrieval = stats.get("total_retrieval_cost", 0) / n
    avg_failure = stats.get("total_failure_cost", 0) / n
    p_reuse = reuse_probability(n)
    stab = stability(stats.get("hash_changes", 0))
    return (p_reuse * avg_retrieval + avg_failure) * stab / max(1, persistent_token_cost)


def candidate_persistent_cost(stats: dict, estimate_tokens) -> int:
    rep = stats.get("representation")
    if rep:
        return estimate_tokens(rep)
    return PLACEHOLDER_REPRESENTATION_TOKENS
