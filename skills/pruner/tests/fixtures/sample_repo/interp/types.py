class Range:
    """A closed numeric interval [lo, hi]."""

    def __init__(self, lo, hi):
        self.lo = lo
        self.hi = hi

    def contains(self, x):
        return self.lo <= x <= self.hi
