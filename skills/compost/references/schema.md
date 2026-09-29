# Output schema

Every `run` / `ingest` prints a human-readable summary by default, or the
same data as JSON with `--json`. The JSON shape (also what's persisted in
`runs/<id>/meta.json`, plus internal fields not shown to the caller):

```yaml
command: str                  # the exact command / label
exit_code: int
duration_seconds: float       # 0.0 for `ingest` (no live execution)
status: pass | fail | timeout | killed | signaled | error
                               # fail if exit_code != 0, any error-severity
                               # event exists, or a numerical check (e.g.
                               # NaN/Inf) flags failure; timeout = --timeout
                               # hit (exit 124); killed = compost got
                               # SIGINT/SIGTERM and forwarded it (exit
                               # 128+N, meta.json `signal`); signaled = the
                               # command itself died from a signal;
                               # error = command could not start (not
                               # found: exit 127, not executable: 126)
profile: str                  # which parser was used (see profiles.md)

root_events:                  # the group of the root error: the first error
                               # (output order) in a user-owned file -- inside
                               # the git root/cwd (relative paths count), not
                               # under /usr, /opt, /Library, /Applications or
                               # an SDK/toolchain, not in vendor/, third_party/,
                               # build/, _deps/, site-packages/, .venv/. Else
                               # the first error with a user-code template
                               # instantiation site; else the first error.
  - event_id: int
    message: str               # representative (first) occurrence, raw text
    count: int                 # how many raw lines collapsed into this group
    lines: "1200-1202,1830"    # compact range of every raw line this group touches
    user_location: "src/main.cpp:14:14"  # only for groups in non-user files: the
                               # user-code site from `required from here` /
                               # `note: ... requested here` (or include chain)
    affected_tests_count: int  # present only for pytest/ctest
    affected_tests: [str]      # capped at 20

warnings:                     # up to 5 unique warning-severity groups
  - <same shape as root_events entries>

repeated_events:              # other error groups, by count (ties: earliest
                               # line), up to 10
  - <same shape as root_events entries>

omitted_error_groups: int     # error groups beyond the caps (see `show --json`)
omitted_warning_groups: int   # warning groups beyond the cap
failing_tests_count: int      # distinct failing test ids (pytest/ctest)
failing_tests: [str]          # first 10 of them
raw_tail: {start_line, lines} | null  # last 40 ANSI-stripped lines, only when
                              # status is fail but no error event was extracted

changed_since_previous_run:   # null on the first run of a given command;
                               # present automatically on every later run of
                               # an identical command string
  previous_run: str            # run_id of the prior run
  exit_code_before: int
  exit_code_after: int
  new_events: [{message, count}]
  resolved_events: [{message, count}]
  count_changes: [{message, before, after}]
  newly_failing_tests_count: int
  newly_failing_tests: [str]   # capped at 20
  newly_passing_tests_count: int
  newly_passing_tests: [str]   # capped at 20
  numerical_summary_before: {...}   # only if both runs had one
  numerical_summary_after: {...}

numerical_summary: dict | null      # profile-specific, see profiles.md

artifacts: [str]              # file paths the run mentioned writing/generating

raw_output_id: str            # e.g. "r0001" -- pass to get/event/grep/diff
raw_line_count: int
retrieval_handles: [str]      # ready-to-run commands (absolute bin/compost path)
```

## Clustering rule (applies to root_events / warnings / repeated_events)

Every candidate event (a line classified error or warning by the active
profile parser, plus any explicit `WARNING:`/`ERROR:`/`FATAL:` line caught
regardless of profile) gets a signature: its message with only *volatile*
tokens normalized — hex addresses (`0xN`), numbers of 6+ digits, timestamps,
temp paths (`/tmp`, `/var/folders`, `pytest-of-*`), PIDs — and whitespace
squashed. Small integers are kept, so `assert 3 == 2` and `assert 1 == 0`
stay separate groups. Compiler diagnostics are keyed per file (file +
normalized message; line:col dropped), so the same template error repeated
60 times across one system header collapses to one group. Test-runner
events are one per failing test; a group lists every affected test id.
The `message` shown is always the original text of the first occurrence,
and `bin/compost event <id> --event N` recovers every raw occurrence.

## Status derivation

`fail` if any of: exit_code != 0, at least one error-severity event group
exists, or the profile's numerical_summary explicitly flags failure (e.g.
`nan_or_inf_detected: true` for the numerical_solver profile). Otherwise
`pass`. compost's own process exit code always propagates the wrapped
command's exit code (for `run`) so it composes normally in shell scripts.

## Storage layout

```
.compost/
  index.json                       # run counter + command -> run_id history
  config.json                      # optional {"keep_per_command": 10, "max_store_mb": 200}
  runs/<run_id>/raw.txt             # verbatim output, streamed as it arrives
  runs/<run_id>/meta.json           # the summary above + internal events_index
                                     # (all clustered groups, not just the
                                     # capped root/warning/repeated lists —
                                     # this is what `event <id> --event N`
                                     # reads from)
```

`meta.json` holds summaries only: per group the first 50 line numbers, a
compact `line_ranges` string and up to 200 test ids. Parsing re-reads
`raw.txt` line by line, so memory stays flat for any output size. A run in
progress has a provisional `status: running` meta.

**Retention** runs under the store lock after every `run`/`ingest` (and via
`bin/compost gc [--keep-per-command N] [--max-store-mb M]`): keep the last
`keep_per_command` runs per command (whitespace-normalized command string),
then evict oldest-first while `runs/` exceeds `max_store_mb` — never the run
just created, the latest run of any command (so the delta always works), or
a run still in progress.

Nothing here is ever summarized by a model. Deleting `.compost/` is safe
at any time — it is pure cache/history, not source of truth for anything
except the diff/delta feature (which just degrades to "no previous run").
