## Why numpy

Linear algebra (eigenvalues, condition numbers), convergence-order
fitting, and confidence intervals are well-trodden numerical ground.
Reimplementing them by hand in this library would make the *verification
tool itself* less numerically trustworthy, not more — and any repository
doing the kind of work this skill targets already depends on numpy in
practice. See `references/modules.md` for exactly what's implemented from
scratch (the PASS/WARN/FAIL threshold logic, convergence-order fitting,
CI-overlap comparison) versus what's a thin wrapper over `numpy.linalg`.
