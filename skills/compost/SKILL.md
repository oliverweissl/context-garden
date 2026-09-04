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
   python3 <this-skill-dir>/scripts/compost.py run -- <command...>
   ```
   (or `<this-skill-dir>/bin/compost run -- <command...>` if executable
   scripts are permitted). This captures stdout+stderr, exit code and
   duration, stores the raw output under `./.compost/`, and prints a
   compact summary: root cause(s), secondary failure groups, warnings,
   any numerical summary, and a `raw_output_id`.

2. **Read the summary, not the raw log.** It contains everything needed for
   first-pass diagnosis: the clustered root error(s) with affected-test
   counts, secondary failure groups, and (for solver-like output) a
   convergence/NaN summary.

3. **Re-running the same command** (e.g. during a fix-test-fix loop)
   automatically reports only what changed — new errors, resolved errors,
   count deltas, residual/convergence deltas — instead of a second full
   summary. Nothing new happened? You'll see "No structural changes."

4. **Only if the summary is insufficient**, pull exactly what's missing
   using the `retrieval_handles` printed with every summary:
   - `compost get <run_id> --lines A:B` — exact raw line range
   - `compost event <run_id> --event N` — full detail + raw excerpt for one clustered event (its id is in the summary)
   - `compost grep <run_id> '<pattern>'` — regex search the raw output
   These never re-run the command; they read the already-stored raw file.

5. Got output from somewhere other than a live run (pasted log, CI
   artifact)? Use `compost ingest --file <path>` (or `--stdin`) instead
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

- Raw output is never discarded — only in `./.compost/runs/<id>/raw.txt`.
- Every clustered event links back to exact source line numbers.
- All parsing is regex/state-machine based — no model calls, fully
  reproducible, works offline.
