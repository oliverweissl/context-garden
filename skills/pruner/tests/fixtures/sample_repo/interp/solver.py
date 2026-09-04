from .core import interpolate
from .types import Range


def solve_step(x, bounds: Range):
    return interpolate(x, bounds.lo, bounds.hi)
