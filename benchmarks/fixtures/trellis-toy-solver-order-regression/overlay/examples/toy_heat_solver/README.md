# toy_heat_solver

A minimal 1D heat-equation diffusion term, used elsewhere in this
example to advance `u_t = alpha * u_xx` via `second_derivative`.

## Recent change

`second_derivative` used to evaluate `f` at `x0-h`, `x0`, and `x0+h`
(the standard centered stencil). It now evaluates `f` at `x0`, `x0+h`,
and `x0+2h` instead, which avoids one function evaluation to the left of
`x0` -- useful when `f` is expensive or only defined for `x >= x0`.
`test_solver.py` still passes after the change.

This directory is a standalone example (not part of the `context_garden`
package); it exists to be reviewed on its own, e.g. as a small PR.
