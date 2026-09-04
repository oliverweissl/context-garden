from .utils import clamp
from .types import Range


def interpolate(x, lo, hi):
    """Linearly interpolate x within [lo, hi], clamping out-of-range input."""
    x = clamp(x, lo, hi)
    span = hi - lo
    if span == 0:
        return 0.0
    return (x - lo) / span
