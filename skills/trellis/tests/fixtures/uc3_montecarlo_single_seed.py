"""UC3: an agent reports an "improvement" based on a single random seed.
Trellis replicates across many seeds and reports the confidence interval,
which is what actually determines whether the claim is supportable.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import trellis as S

BASELINE = 100.0


def metric(seed):
    """Simulates a noisy benchmark metric with NO true underlying
    improvement (mean stays at BASELINE) -- any single-seed reading that
    looks like an improvement is purely sampling noise."""
    rng = np.random.RandomState(seed)
    return BASELINE + rng.normal(0, 5.0)


# the single seed the agent happened to run with, which looked like a win:
_single_seed_reading = metric(seed=1)

RESULTS = [
    S.stochastic.seed_replication_check(metric, seeds=list(range(20)), name="benchmark_metric"),
]

REMAINING_RISKS = [
    f"A single-seed reading of {_single_seed_reading:.2f} (seed=1) was what originally prompted this "
    "check -- see seed_replication_check's mean/CI above for whether that reading is representative.",
]
