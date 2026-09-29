# Output schema

Fields are as printed by `--json` / `show <id> --json`; only non-obvious
semantics are listed here.

## Status

- `pass` — exit 0 and no error-severity event group.
- `fail` — exit != 0, or at least one error group (e.g. a `nan`/`inf`
  residual in numerical_solver; a stray `NaN` token elsewhere is only a warning).
- `timeout` — `--timeout` hit; process group SIGKILLed, exit 124.
- `killed` — compost got SIGINT/SIGTERM and forwarded it to the command's
  process group (a second signal escalates to SIGKILL); exit 128+N.
- `signaled` — the command itself died from a signal; exit 128+N.
- `error` — command could not start: not found (127) / not executable (126).

`run` always propagates the wrapped command's exit code.
Output is streamed to disk as it arrives, so a timed-out or interrupted run
still gets a summary of its partial output.

## Selection and caps

- **root_events**: the first error in output order located in a user-owned
  file — inside the git root (relative paths count), not under
  `/usr`, `/opt`, `/Library`, `/Applications`, an SDK/toolchain, nor in
  `vendor/`, `third_party/`, `build/`, `_deps/`, `site-packages/`, `.venv/`.
  Else the first error with a user-code instantiation site; else the first
  error. Frequency never picks the root.
- `user_location`: for a group in a non-user file (e.g. template spam in a
  system header), the user-code site from `required from here` /
  `requested here` or the include chain.
- `repeated_events`: up to 10 other error groups, by count. `warnings`: up
  to 5 groups. The rest are counted in `omitted_*_groups`; `event <id>
  --event N` reaches any group.
- `failing_tests`: first 10; `affected_tests` / `newly_*_tests`: first 20.
- `raw_tail`: last 40 lines, only when status is not `pass`, nothing was
  extracted, and output exceeds 30 lines.

## Clustering

Only volatile tokens (hex addresses, 6+ digit numbers, timestamps, temp
paths, PIDs) are normalized; small integers are kept, so `assert 3 == 2`
and `assert 1 == 0` stay separate groups. Compiler diagnostics are keyed
per file with line:col dropped.

## Delta

`changed_since_previous_run` appears only when the same command string
(whitespace-normalized) was run before: `pytest tests/` and
`pytest tests/ -x` are different histories.

## Retention

Runs live in `.compost/runs/<id>/` (raw.txt + meta.json) at the git root.
After every `run`/`ingest` (or `bin/compost gc [--keep-per-command N]
[--max-store-mb M]`): keep the last 10 runs per command, then evict
oldest-first while `runs/` exceeds 200 MB — never the run just created, the
latest run of any command (so the delta keeps working), or a run in
progress. Defaults come from `.compost/config.json`
(`{"keep_per_command": 10, "max_store_mb": 200}`). Deleting `.compost/` is
safe; the delta just restarts.
