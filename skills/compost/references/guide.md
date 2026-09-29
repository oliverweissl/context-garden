# compost: operational details

Moved out of `SKILL.md` to keep the on-trigger body small.

## Running

`run` streams stdout+stderr straight to
`<git root>/.compost/runs/<id>/raw.txt`, so partial output survives a
timeout, Ctrl-C or SIGTERM -- the signal is forwarded to the command's
process group and recorded as `status=KILLED`. For template spam from
system headers the summary reports the user-code call site.

Never pipe large output straight into context; run it through compost first.

Example with a shell pipeline:
`bin/compost run -- bash -c 'make 2>&1 | tee build.log'`.

## Retrieval handles

Printed with every summary as full, copy-pasteable commands:

- `bin/compost get <run_id> --lines A:B` -- exact raw line range
- `bin/compost event <run_id> --event N` -- full detail + raw excerpt for one clustered event
- `bin/compost grep <run_id> '<pattern>'` -- regex search the raw output
- `bin/compost show <run_id> --json` -- every group, incl. ones capped as "+N more"

## Guarantees

- Raw output is kept verbatim in `.compost/runs/<id>/raw.txt`. Retention
  keeps the last 10 runs per command and caps the store at 200 MB
  (oldest first; never the latest run of a command), configurable in
  `.compost/config.json` (`keep_per_command`, `max_store_mb`);
  `bin/compost gc` applies it on demand.
- Every clustered event links back to exact source line numbers.
- All parsing is regex/state-machine based -- no model calls, fully
  reproducible, works offline.

## Rules, verbatim

- `compost` is not on PATH -- always use the full `bin/compost` path, or
  `python3 <this-skill-dir>/scripts/compost.py` if executables are not
  permitted.
- Retrieval handles never re-run the command; they read the already-stored raw file.
