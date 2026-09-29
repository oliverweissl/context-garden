# CLI reference

Store: `.pruner/index.sqlite` (a cache; any version/parser change triggers
a full rebuild) and `.pruner/slices/<id>.json` (saved results for
`show`/`expand`).

## Global options (must come before the subcommand)

| flag | meaning |
|---|---|
| `--repo PATH` | repository root (default: cwd) |
| `--store PATH` | store directory (default: `<repo>/.pruner`) |
| `--parser {auto,builtin,tree-sitter}` | symbol extraction backend (default `auto`) |

`bin/pruner --parser builtin select ...` works; `select --parser ...` is
an argparse error.

### Parser backends

- `builtin`: Python via stdlib `ast`, C/C++ via a regex/brace-counting
  parser. Always available.
- `tree-sitter`: used when `tree_sitter`, `tree_sitter_python` and
  `tree_sitter_cpp` are importable (`pip install tree-sitter
  tree-sitter-python tree-sitter-cpp`; never required). Error tolerant and
  real C/C++ syntax; inline C++ methods get a `Class::method` qualname.
  Supports py-tree-sitter 0.22-0.25 (0.26.0 segfaults on node access and
  is treated as unavailable). A file that fails to parse falls back to
  builtin.
- `auto` picks `tree-sitter` when available, else `builtin`;
  `--parser tree-sitter` without the packages is an error. **Switching
  backends (including running `auto` from environments with and without
  the packages) rebuilds the index.**

## `pruner index [--force]`

Parses Python (`.py`) and C/C++ (`.c/.h/.cc/.cpp/.cxx/.hpp/.hh`) files
(via `git ls-files` when available, so `.gitignore` applies; files over
2MB skipped); everything else is classified `config`, `doc` or `other`.
Incremental. `select` runs it automatically; run it directly only for the
report.

## `pruner select --task TEXT [options]`

| flag | meaning |
|---|---|
| `--budget N` | hard token ceiling on `used_tokens` (default 2000) |
| `--changed F [F ...]` | file paths to boost as "recently relevant" |
| `--auto-changed` | use `git diff --name-only HEAD` if `--changed` omitted |
| `--error TEXT` / `--error-file PATH` | compiler error / traceback text; locations parsed from it seed selection directly |
| `--graph PATH` | external `{"edges": [{"from": id, "to": id}]}` JSON merged into the call graph (additive, never required) |
| `--json` | machine-readable output (the slice is always saved regardless) |

Chunk ids are `"<rel_path>:<qualname>"` (`@<start_line>` appended for a
repeated qualname), `"<rel_path>:__file__"` or `"<rel_path>:A-B"`.

- `confidence`: `high` only if a non-test source chunk landed in
  `required_context`; `medium` if only supporting/test chunks; else `low`
  (then verify with grep/reads).
- `required_context` = score >= 8.0, not a test/config file.
- Tests the task/error doesn't name are capped at 20% of the budget.

## `pruner expand <slice_id> [options]`

| flag | meaning |
|---|---|
| `--add ID [ID ...]` | promote `omitted_candidates` entries (by `id`, or `file:start-end`) into `supporting_context`; a `file:start-end` matching no candidate is added ad hoc |
| `--file PATH --lines A:B` | inject an arbitrary chunk the scorer never surfaced |
| `--reason TEXT` | recorded reason for a `--file`/`--lines` add |
| `--budget-extra N` | raise this slice's budget by N before checking whether the add fits |

An add that would exceed `budget` is refused (reported) unless
`--budget-extra` raises the ceiling. Does not re-run scoring.

## `pruner show <slice_id> [--json]` / `pruner list`

`show` re-prints a saved slice (including `expand` adds) without
re-scoring; `list` enumerates saved slices.
