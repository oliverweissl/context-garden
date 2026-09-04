from interp.solver import solve_step
from interp.types import Range


def test_solve_step_within_bounds():
    assert solve_step(5, Range(0.0, 10.0)) == 0.5
