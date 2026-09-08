import math

from solver import second_derivative


def test_second_derivative_matches_known_function():
    # f(x) = sin(x), so f''(x) = -sin(x) exactly -- a convenient
    # closed-form reference. Single resolution, loose tolerance: this
    # only checks the approximation is in the right ballpark, not that
    # it converges at the expected rate as h shrinks.
    x0 = 0.5
    h = 0.05
    approx = second_derivative(math.sin, x0, h)
    exact = -math.sin(x0)
    assert abs(approx - exact) < 0.12
