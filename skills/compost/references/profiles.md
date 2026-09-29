# Profiles

Detection order (first match wins) unless `--profile` is passed:

1. **pytest** — command contains `pytest`, or output has
   `short test summary info` / a `=== FAILURES ===` banner. One event per
   failing test (so groups carry `affected_tests`);
   `numerical_summary = {tests_passed, tests_failed}`.
2. **ctest** — command contains `ctest`, or output has
   `The following tests FAILED` / `tests failed out of`. One event per
   failing test.
3. **cmake** — output has a `CMake Error` / `CMake Warning` block, or the
   command contains `cmake` but not `--build` (`cmake --build` is a build
   driver and falls through to gcc). One event per block.
4. **slurm** — command contains `srun`/`sbatch`, or output has `slurmstepd`
   or the word `slurm`. Checked before gcc so `slurmstepd: error:` lines
   aren't read as compiler diagnostics. Events for OOM-kill, time/memory
   limit, cancellation, step errors;
   `numerical_summary = {termination_reason, peak_memory}`.
5. **gcc** — command is a `gcc`/`g++`/`clang`/`clang++`/`cc`/`c++`
   invocation, or any line looks like `path:line:col: error|warning: msg`.
   `note:` lines are not events; instantiation backtraces and include
   chains supply `user_location`.
6. **python_traceback** — output has `Traceback (most recent call last):`.
   One event per traceback (its final exception line); use
   `event <id> --event N` for the frames.
7. **numerical_solver** — a line like `iter <N> ... resid <value>`.
   `numerical_summary = {iterations_observed, initial_residual,
   final_residual, trend, nan_or_inf_detected}`, `trend` one of
   `converging`/`diverging`/`oscillatory`/`mixed`/`insufficient_data`.
   A `nan`/`inf` residual is an error (→ `fail`); a bare `NaN`/`Inf` token
   elsewhere is only a warning but still sets `nan_or_inf_detected`.
8. **generic** — `error|exception|fatal|failed|failure` lines are errors
   only on non-zero exit (warnings on exit 0); zero counts like `0 failed`
   or `no errors` are ignored. `warning` lines are warnings.

Regardless of which profile is active, any line starting with `WARNING:`,
`ERROR:`, or `FATAL:` is always captured as an event.
