# Report schema and CLI reference

## CheckResult

Every check returns one (`trellis/schema.py`):

```yaml
name:        # e.g. "residual_norm:solver_residual" -- module-qualified by convention
status:      # PASS | WARN | FAIL
category:    # implementation | numerical | model_validation | empirical
metric:      # dict of the raw computed numbers
expected:    # what was required to PASS
observed:    # what was actually measured
evidence:    # supporting raw data (arrays, samples, ...) -- truncated where large
notes:       # human-readable explanation, always populated for WARN/FAIL
config:      # strictness configuration (tol, expected_order, alpha, resolutions,
             # seeds, reference fingerprints, ...) -- what `trellis lock` pins
```

`config` is recorded automatically for every public check: scalar/list
arguments incl. defaults, not the data under test. Reference data is
stored as `{"sha256", "shape", "fingerprint"}` (the float fingerprint
ignores last-ulp platform noise); for callable references it is taken
over the *evaluated* values, so changing the reference's behaviour is
detected.

### The four categories

- **implementation** — the code behaves as coded (reproducibility,
  parameter sanity, termination flags); what unit tests already give you.
- **numerical** — internally self-consistent: residuals, convergence
  order, conditioning, gradient check. trellis's core.
- **model_validation** — compared against a *known-correct* answer
  (analytic, manufactured, reference implementation); self-consistent
  code can still solve the wrong model.
- **empirical** — validated across multiple seeds with a confidence
  interval, not one run.

## Report

```yaml
status:              # worst of any individual check's status (FAIL > WARN > PASS)
checks: [CheckResult, ...]
unsupported_claims:  # claims this report does NOT support making
remaining_risks:      # known gaps in what was verified
created_at:
```

`build_report(results, unsupported_claims=None, remaining_risks=None, auto_gaps=True)`:
with `auto_gaps=True` (default) a `remaining_risks`/`unsupported_claims`
entry is appended for each category **entirely absent** from `results`.
`auto_gaps=False` suppresses it (e.g. a deliberately implementation-only
spec).

## CLI (`scripts/trellis.py`)

### `trellis run <spec.py> [--save PATH] [--json] [--allow-warn] [--update-lock] [--save-baseline] [--baseline SHA|latest] [--regression-factor F] [--trellis-dir DIR]`

Executes `spec.py` as a normal Python script; it must define module-level
`RESULTS: list[CheckResult]`, optionally `UNSUPPORTED_CLAIMS` /
`REMAINING_RISKS: list[str]`. Saves the report to `--save` (default
`<spec_dir>/.trellis/<spec_stem>_report.json`). Exit: `1` FAIL, `3` WARN
(WARN = not verified; `0` with `--allow-warn`), else `0`. Relative
result-file paths resolve against the cwd, then the spec's directory.

Two CLI-generated checks (category `implementation`) are appended:

- `spec_lock:<spec>` — compares every check's `config` with the spec's
  entry in `<trellis-dir>/spec.lock`:
  - **FAIL** on anything looser: larger `tol`/`tol_order`/`rtol`/`atol`/
    `tol_spread`/`floor_rtol`/`f_rtol`/`fd_safety`/`confidence`/
    `warn_threshold`/`fail_threshold`; smaller `expected_order`/`alpha`/
    `n_samples`/`n_runs`; `cond` no longer passed; a pinned value set to
    `None`; a check removed; a *must-match* key changed (`reference`,
    `expected*`, resolutions/`param_values`/`dts`, `seeds`, `param_kind`,
    `relative`, `ord`, `direction`, `assume_normal`). Note on `alpha`:
    every trellis test FAILs only when it *rejects*, so a smaller alpha
    rejects less often — that is the looser direction.
  - **PASS** with the change listed for tighter changes / new checks;
    other keys (e.g. `eps`, file templates) are reported as `[changed]`.
  - **WARN** "unpinned spec" when the lock has no entry for this spec.
  - `--update-lock` rewrites the entry (WARN if it loosened anything,
    so it never looks silently green). **Human-only** — review the
    `git diff` of the lock before committing.
- `baseline:<sha>` (only with `--baseline`) — compares metrics with
  `<trellis-dir>/baselines/<sha>.json` (`latest` = most recently saved
  for this spec; a sha prefix works): FAIL if `observed_order` dropped by
  more than the check's `tol_order` (or became uncomputable), or an
  error/residual metric (`finest_error`, `relative_residual`,
  `residual_norm`, `relative_error`, `max_relative_drift`,
  `max_relative_error`, `max_violation`, `max_asymmetry`, `spread`,
  `residual`, `error`, ...) grew by more than `--regression-factor`
  (default 2) and above 1e-13. Missing baseline → WARN.

`--save-baseline` writes the current metrics to
`<trellis-dir>/baselines/<git HEAD sha>.json` (records whether the tree was
dirty); requires a git repository.

`--trellis-dir` defaults to `<git root>/.trellis` (or `<spec_dir>/.trellis`
outside git). Commit `spec.lock`; baselines are optional; reports are
throwaway. Reports are written next to the spec (`<spec_dir>/.trellis/`),
so a spec in a subdirectory needs the `**/` line:

```gitignore
.trellis/*
!.trellis/spec.lock
# !.trellis/baselines/   # if you want per-commit baselines shared
**/.trellis/*_report.json
```

### `trellis lock <spec.py> [--trellis-dir DIR]`

Runs the spec and writes its checks' `config` to `spec.lock`
(`{"version": 1, "specs": {"<path relative to git root>": {"locked_at",
"checks": {"<check name>": {"category", "config"}}}}}`; duplicate check
names get `#2`, `#3`, ...). Other specs' entries are preserved.

### `trellis list-modules`

Lists each module's public checks with their first docstring line
(re-exported helpers excluded).

### `trellis show <report.json>`

Re-prints a saved report without re-running.
