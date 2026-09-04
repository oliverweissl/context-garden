# Output schema

Every `run` / `ingest` prints a human-readable summary by default, or the
same data as JSON with `--json`. The JSON shape (also what's persisted in
`runs/<id>/meta.json`, plus internal fields not shown to the caller):

```yaml
command: str                  # the exact command / label
exit_code: int
duration_seconds: float       # 0.0 for `ingest` (no live execution)
status: pass | fail           # fail if exit_code != 0, any error-severity
                               # event exists, or a numerical check (e.g.
                               # NaN/Inf) flags failure
profile: str                  # which parser was used (see profiles.md)

root_events:                  # top 1 error group, ranked by occurrence
                               # count (ties broken by earliest line)
  - event_id: int
    message: str               # representative (first) occurrence, raw text
    count: int                 # how many raw lines collapsed into this group
    lines: "1200-1202,1830"    # compact range of every raw line this group touches
    affected_tests_count: int  # present only for pytest/ctest
    affected_tests: [str]      # capped at 20

warnings:                     # up to 5 unique warning-severity groups
  - <same shape as root_events entries>

repeated_events:              # error groups beyond root_events, up to 10
  - <same shape as root_events entries>

changed_since_previous_run:   # null on the first run of a given command;
                               # present automatically on every later run of
                               # an identical command string
  previous_run: str            # run_id of the prior run
  exit_code_before: int
  exit_code_after: int
  new_events: [{message, count}]
  resolved_events: [{message, count}]
  count_changes: [{message, before, after}]
  numerical_summary_before: {...}   # only if both runs had one
  numerical_summary_after: {...}

numerical_summary: dict | null      # profile-specific, see profiles.md

artifacts: [str]              # file paths the run mentioned writing/generating

raw_output_id: str            # e.g. "r0001" -- pass to get/event/grep/diff
raw_line_count: int
retrieval_handles: [str]      # ready-to-run compost commands for this run
```

## Clustering rule (applies to root_events / warnings / repeated_events)

Every candidate event (a line classified error or warning by the active
profile parser, plus any explicit `WARNING:`/`ERROR:`/`FATAL:` line caught
regardless of profile) is normalized — digits collapsed to `#`, hex
addresses to `0xN`, whitespace squashed — and grouped by that normalized
signature. This is why 20,000 near-identical errors that differ only by
line number, iteration count or address collapse into a handful of groups.
The `message` shown is always the *original, unnormalized* text of the
first occurrence, so nothing about the collapse is lossy for diagnosis
purposes — and `compost event <id> --event N` recovers every raw
occurrence with line numbers on request.

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
  runs/<run_id>/raw.txt             # verbatim captured output, never mutated
  runs/<run_id>/meta.json           # the summary above + internal events_index
                                     # (all clustered groups, not just the
                                     # capped root/warning/repeated lists —
                                     # this is what `event <id> --event N`
                                     # reads from)
```

Nothing here is ever summarized by a model. Deleting `.compost/` is safe
at any time — it is pure cache/history, not source of truth for anything
except the diff/delta feature (which just degrades to "no previous run").
