"""Deterministic promotion scoring.

value = ((distinct_sessions - 1) * avg_retrieval_cost + total_failure_cost)
        * stability * decay / max(1, persistent_token_cost)

decay = 0.5 ** (days_since_last_access / half_life_days)   (1.0 if critical)

All quantities are heuristic estimates (see references/scoring.md for the
reasoning behind each term) -- there is no ground truth "correct" value,
only a consistent, explainable ranking.
"""

from __future__ import annotations

import time

PLACEHOLDER_REPRESENTATION_TOKENS = 20  # used only to rank un-promoted candidates
DEFAULT_HALF_LIFE_DAYS = 30.0
DEFAULT_FACT_FAILURE_COST = 60
SECONDS_PER_DAY = 86400.0


def distinct_sessions(stats: dict) -> int:
    """Distinct sessions that observed this key. Keys recorded before
    session tracking existed have no `session_count`; each of their
    accesses is counted as its own session (what the old formula assumed)."""
    if "session_count" in stats:
        return max(1, stats["session_count"])
    return max(1, stats.get("access_count", 1))


def stability(hash_changes: int) -> float:
    """1.0 if the backing source(s) never changed across observations (or
    there are no sources to hash, e.g. a stated policy/invariant); decays
    as the source content churns underneath repeated observations."""
    return 1.0 if hash_changes <= 0 else 1.0 / (1.0 + hash_changes)


def decay(last_access: float | None, half_life_days: float, now: float | None = None,
          critical: bool = False) -> float:
    """Exponential forgetting: halves every `half_life_days` without an
    access. Critical facts (and half_life_days <= 0) never decay."""
    if critical or half_life_days <= 0 or last_access is None:
        return 1.0
    now = time.time() if now is None else now
    days = max(0.0, (now - last_access) / SECONDS_PER_DAY)
    return 0.5 ** (days / half_life_days)


def failure_total(stats: dict, fact_failure_cost: float = DEFAULT_FACT_FAILURE_COST) -> float:
    """Summed failure cost. A declared `fact` recorded before facts got a
    default failure cost (legacy: no session_count, zero failure) is
    treated as one declaration at the configured default."""
    total = stats.get("total_failure_cost", 0)
    if total == 0 and stats.get("kind") == "fact" and "session_count" not in stats:
        return fact_failure_cost
    return total


def compute_value(
    stats: dict,
    persistent_token_cost: int,
    *,
    now: float | None = None,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
    critical: bool = False,
    fact_failure_cost: float = DEFAULT_FACT_FAILURE_COST,
) -> float:
    n = max(1, stats.get("access_count", 1))
    avg_retrieval = stats.get("total_retrieval_cost", 0) / n
    repeats = distinct_sessions(stats) - 1  # the first discovery is sunk cost
    failure = failure_total(stats, fact_failure_cost)
    stab = stability(stats.get("hash_changes", 0))
    d = decay(stats.get("last_access"), half_life_days, now, critical)
    return (repeats * avg_retrieval + failure) * stab * d / max(1, persistent_token_cost)


def value_kwargs(config: dict) -> dict:
    """compute_value keyword args taken from a loaded config.json."""
    return {
        "half_life_days": float(config.get("half_life_days", DEFAULT_HALF_LIFE_DAYS)),
        "fact_failure_cost": float(config.get("fact_failure_cost", DEFAULT_FACT_FAILURE_COST)),
    }


def candidate_persistent_cost(stats: dict, estimate_tokens) -> int:
    rep = stats.get("representation")
    if rep:
        return estimate_tokens(rep)
    return PLACEHOLDER_REPRESENTATION_TOKENS
