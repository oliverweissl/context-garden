# Profiles

A profile is a pair of (detection rule, parser). Detection runs in this
order (first match wins) unless `--profile` is passed explicitly:

1. **pytest** — command contains `pytest`, or output contains
   `short test summary info` or a `===== FAILURES =====` banner.
   Parses the `short test summary info` block (`FAILED path::test - reason`)
   for one event per failing test, with `test` set so groups report
   `affected_tests`/`affected_tests_count`. Falls back to scanning inline
   `E   ExceptionType: message` lines if no summary block is found.
   `numerical_summary` = `{tests_passed, tests_failed}` parsed from the
   pytest tail line.

2. **ctest** — command contains `ctest`, or output contains
   `the following tests failed` / `tests failed out of`.
   Parses the `N - test_name (Failed)` lines under "The following tests
   FAILED:".

3. **cmake** — command contains `cmake`, or output contains a
   `CMake Error at file:line` / `CMake Warning at file:line` block. Each
   block (the header line plus its indented message lines) becomes one
   event.

4. **slurm** — command contains `srun`/`sbatch`, or output contains
   `slurmstepd` or the word `slurm`. Checked *before* the gcc profile,
   because SLURM's own `slurmstepd: error: ...` lines would otherwise match
   the generic `file:line: error:` heuristic. Detects, in priority order:
   OOM-kill, time-limit-exceeded, memory-limit-exceeded, cancelled,
   generic step error — one event per line (first matching pattern wins, so
   a line matching two patterns is never double-counted). Also extracts
   `MaxRSS=`/`MaxVMSize=` as `peak_memory`.
   `numerical_summary` = `{termination_reason, peak_memory}`.

5. **gcc** — command contains `gcc`/`g++`/`clang`/`clang++`/`cc`/`c++`, or
   any line matches the diagnostic shape `path:line:col: error|warning: msg`.
   `note:` lines are dropped (they're context for a preceding
   error/warning, not independent events).

6. **python_traceback** — output contains a bare
   `Traceback (most recent call last):` line. Each traceback's final
   (exception) line becomes one event; intermediate frames are not
   individually extracted (use `compost event <id> --event N` to see the
   full raw traceback around that line).

7. **numerical_solver** — output has a line matching
   `iter(ation)? <N> ... resid ... <value>` (case-insensitive). Extracts
   the residual sequence and computes:
   `{iterations_observed, initial_residual, final_residual, trend, nan_or_inf_detected}`.
   `trend` is one of `converging` / `diverging` / `oscillatory` / `mixed` /
   `insufficient_data`, from the sign pattern of successive residual
   deltas (oscillatory: ≥40% of consecutive deltas flip sign over ≥3
   samples; converging/diverging: one direction outnumbers the other ≥2:1;
   otherwise mixed). A `nan`/`inf` residual, or a bare `NaN`/`Inf`/`-Inf`
   token anywhere in the output, sets `nan_or_inf_detected: true` and
   forces `status: fail` regardless of exit code.

8. **generic** (fallback) — lines matching
   `\b(error|exception|fatal|failed|failure)\b` (case-insensitive) become
   error events; lines matching `\bwarning\b` become warning events.

## Cross-profile supplement

Regardless of which profile is active, any line starting with
`WARNING:`, `ERROR:`, or `FATAL:` (optionally indented) is always captured
as an event. This exists because e.g. the numerical_solver profile only
looks for residual/NaN patterns — a solver that also prints
`WARNING: slow convergence detected` would otherwise have that warning
silently dropped just because the active profile wasn't looking for it.
Lines already captured by the profile parser (same line number) are not
duplicated.

## Adding a profile

Add a `parse_<name>(lines, exit_code) -> {events, numerical_summary,
artifacts}` function and a detection branch in `co_profiles.py`, then
register it in the `PARSERS` dict. Keep it regex/state-machine based —
compost's core guarantee is that compaction never requires a model call.
