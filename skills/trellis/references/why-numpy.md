## Why numpy

Linear algebra (eigenvalues, condition numbers), convergence-order
fitting, and confidence intervals are well-trodden numerical ground.
Reimplementing them by hand in this library would make the *verification
tool itself* less numerically trustworthy, not more — and any repository
doing the kind of work this skill targets already depends on numpy in
practice. See `references/modules.md` for exactly what's implemented from
scratch (the PASS/WARN/FAIL threshold logic, convergence-order fitting,
CI-overlap comparison) versus what's a thin wrapper over `numpy.linalg`.

scipy is **optional**: it is imported lazily, only when a
`scipy.sparse` matrix or `LinearOperator` is passed to a `linalg` check
(sparse norms, `onenormest`, `splu`, `eigsh`). Everything else — including
all statistics (t, chi-square, KS) — is numpy/stdlib-only.
