---
name: compost
description: Capture and compact-summarize large, repetitive tool output (compiler builds, pytest/ctest runs, SLURM/HPC jobs, iterative solver logs) instead of reading raw output into context. Use before running or re-running any command whose output could be long or repetitive, or when only what changed since the last run matters.
---

# compost

Deterministic, offline compaction for command output. No LLM is used to
summarize — output is parsed, clustered, and compressed by script, and the
full raw output is always kept on disk and retrievable. Never pipe large
output straight into context; run it through compost first.

## Workflow

1. **Run the command through compost** instead of a bare shell call:
   ```
   <this-skill-dir>/bin/compost run -- <command...>
   ```
   (`compost` is not on PATH -- always use the full `bin/compost` path, or
   `python3 <this-skill-dir>/scripts/compost.py` if executables are not
   permitted). Pipes/redirects need a shell:
   `bin/compost run -- bash -c 'make 2>&1 | tee build.log'`. This streams
   stdout+stderr straight to `<git root>/.compost/runs/<id>/raw.txt` (so
   partial output survives a timeout, Ctrl-C or SIGTERM -- the signal is
   forwarded to the command's process group and recorded as
   `status=KILLED`), then prints a compact summary: the root error (first
   error in a user-owned file, with the user-code call site for template
   spam from system headers), secondary failure groups, warnings, any
   numerical summary, and a `raw_output_id`.

2. **Read the summary, not the raw log.** It contains everything needed for
   first-pass diagnosis: the clustered root error(s) with affected-test
   counts, secondary failure groups, and (for solver-like output) a
   convergence/NaN summary.

3. **Re-running the same command** (e.g. during a fix-test-fix loop)
   automatically reports only what changed — new errors, resolved errors,
   count deltas, residual/convergence deltas — instead of a second full
   summary. Nothing new happened? You'll see "No structural changes."

4. **Only if the summary is insufficient** (or looks empty/thin for a
   failure), pull exactly what's missing using the `retrieval_handles`
   printed with every summary -- they are full, copy-pasteable commands:
   - `bin/compost get <run_id> --lines A:B` — exact raw line range
   - `bin/compost event <run_id> --event N` — full detail + raw excerpt for one clustered event (its id is in the summary)
   - `bin/compost grep <run_id> '<pattern>'` — regex search the raw output
   - `bin/compost show <run_id> --json` — every group, incl. ones capped as "+N more"
   These never re-run the command; they read the already-stored raw file.

5. Got output from somewhere other than a live run (pasted log, CI
   artifact)? Use `bin/compost ingest --file <path>` (or `--stdin`) instead
   of `run` — same parsing/clustering/storage pipeline.

## When NOT to bother

Short, non-repetitive output (a handful of lines) doesn't need compost —
just read it. Reach for compost when output is long, likely to repeat, or
you're about to re-run the same command.

## Supported profiles (auto-detected, override with `--profile`)

`pytest`, `ctest`, `gcc` (also clang), `cmake`, `python_traceback`, `slurm`,
`numerical_solver`, `generic` (fallback: WARNING:/ERROR:/FATAL:-style lines
and generic error/warning keywords).

Full output schema: `references/schema.md`. Per-profile detection rules and
extraction details: `references/profiles.md`.

## Guarantees

- Raw output is kept verbatim in `.compost/runs/<id>/raw.txt`. Retention
  keeps the last 10 runs per command and caps the store at 200 MB
  (oldest first; never the latest run of a command), configurable in
  `.compost/config.json` (`keep_per_command`, `max_store_mb`);
  `bin/compost gc` applies it on demand.
- Every clustered event links back to exact source line numbers.
- All parsing is regex/state-machine based — no model calls, fully
  reproducible, works offline.
