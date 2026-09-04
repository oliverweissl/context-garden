from interp.core import interpolate


def test_boundary_clamped_low():
    assert interpolate(-5, 0.0, 10.0) == 0.0


def test_boundary_clamped_high():
    assert interpolate(15, 0.0, 10.0) == 1.0


def test_midpoint():
    assert interpolate(5, 0.0, 10.0) == 0.5
