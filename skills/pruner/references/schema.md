# CLI and record reference

## Storage layout

```
.pruner/
  index.json        the repository index (files, symbols, call/reference
                     edges, test links) -- see below
  slices/
    s0001.json       a saved selection result, for `show`/`expand`
    ...
```

## `pruner index [--force]`

Walks the repo (via `git ls-files` when available, so `.gitignore` is
respected for free; otherwise a hardcoded ignore list — build/vendor/venv/
cache directories, `.pyc`/`.so`/binary-looking files, anything over 2MB),
parses every Python (`.py`) and C/C++ (`.c/.h/.cc/.cpp/.cxx/.hpp/.hh`) file,
and classifies everything else as `config` (CMakeLists.txt, pyproject.toml,
Makefile, `*.yaml`/`.toml`/`.ini`/`.cfg`, ...), `doc` (`.md`/`.rst`/`.txt`),
or `other`. **Incremental**: a file whose content hash matches the previous
index run is reused verbatim, not re-parsed; import/call-edge resolution
always reruns over the full current file set regardless (a new file can
change what an unrelated, unchanged file's imports resolve to).

`select` runs this automatically if `.pruner/index.json` doesn't exist
yet; run `index` directly only when you want the report (files parsed vs.
reused, symbol/edge counts) without also running a selection.

### Index record shape (`.pruner/index.json`)

```yaml
files:
  <rel_path>:
    hash: sha256[:16] of the file's content
    kind: python | cpp | test | config | doc | other
    language: python | cpp | null
    size_tokens: chars/4 estimate for the whole file
    line_count:
    imports: [module/header names as written in the source]
    resolved_imports: [rel_path, ...]   # only ones that resolved to an in-repo file
    symbols:
      - id: "<rel_path>:<qualname>"
        name: "interpolate"
        qualname: "Solver.run"          # dotted for Python nesting, :: for C++
        type: function | class | type   # 'type' = C/C++ struct/class/enum
        start_line: / end_line:
        doc: first line of the docstring, Python only
        calls: [names referenced -- see "Reference resolution" below]

symbol_index: {name: [symbol_id, ...]}   # for name-based call resolution
call_edges: [{from: symbol_id, to: symbol_id, precision: ...}]
test_links: {source_rel_path: [test_rel_path, ...]}
```

### Reference resolution ("Reference resolution" = how a name in `calls`
becomes an edge in `call_edges`)

Name-based, not type-checked -- in priority order: (1) another symbol in
the **same file**, (2) a uniquely-named symbol among the files this file's
imports **resolved** to, (3) a symbol with that name **anywhere** in the
repo, if the name is unique repo-wide (flagged `precision: unique_name_repo_wide`,
the least trustworthy tier), otherwise the reference is dropped as
`ambiguous` (multiple same-named candidates, no way to disambiguate without
type information). A symbol's `calls` list includes not just actual calls
but any name it plausibly *references*: Python parameter/return type
annotations, and C/C++ signature identifiers (return type + parameter
types) — both deliberately over-inclusive (parameter/variable names get
swept in too) since anything that fails to resolve to a real symbol is
silently dropped and costs nothing.

## `pruner select --task TEXT [options]`

| flag | meaning |
|---|---|
| `--budget N` | hard token ceiling on `used_tokens` (default 2000) |
| `--changed F [F ...]` | file paths to boost as "recently relevant" |
| `--auto-changed` | use `git diff --name-only HEAD` if `--changed` omitted |
| `--error TEXT` / `--error-file PATH` | compiler error / traceback text; locations parsed from it seed selection directly |
| `--graph PATH` | external `{"edges": [{"from": id, "to": id}]}` JSON to merge into the call graph (additive, never required) |
| `--json` | machine-readable output (also always saved to `.pruner/slices/<id>.json` regardless of this flag) |

### Output record

```yaml
task:
budget:
used_tokens:
confidence: high | medium | low   # high if anything landed in required_context or relevant_tests

required_context: [chunk, ...]     # score >= 8.0, not a test/config file
supporting_context: [chunk, ...]   # score > 0, below the required threshold
relevant_tests: [chunk, ...]       # any selected chunk whose file is classified 'test'
relevant_config: [chunk, ...]      # any selected chunk whose file is classified 'config'
omitted_candidates: [chunk, ...]   # scored but not selected (score <= 0, or budget ran out); capped at 30, sorted by score desc

slice_id:    # only in the CLI's own output, not part of the scoring record itself
```

Each `chunk`:
```yaml
id: "<rel_path>:<qualname>"   # or "<rel_path>:__file__" for a whole-file chunk
file:
start_line: / end_line:
chunk_kind: function | class | type | file | adhoc
score:
reasons: [human-readable strings explaining every scoring component that fired]
tokens: chars/4 estimate of that exact line range
is_seed: whether this chunk directly seeded the graph BFS (exact task-name match, or an error-stack location)
```

## `pruner expand <slice_id> [options]`

| flag | meaning |
|---|---|
| `--add ID [ID ...]` | promote specific `omitted_candidates` entries (by their `id`) into `supporting_context` |
| `--file PATH --lines A:B` | inject an arbitrary chunk the scorer never surfaced at all (any file, any range) |
| `--reason TEXT` | recorded reason for a `--file`/`--lines` add |
| `--budget-extra N` | raise this slice's budget by N before checking whether the add fits |

Both modes are still budget-enforced: an add that would push `used_tokens`
over `budget` is refused (reported, not silently applied) unless
`--budget-extra` raises the ceiling first. This does not re-run scoring —
it's a targeted, cheap promotion of one specific thing, which is the point
(see SKILL.md's "if you hit a missing dependency" step).

## `pruner show <slice_id> [--json]` / `pruner list`

`show` re-prints a saved slice (including anything added via `expand`)
without re-scoring. `list` enumerates all saved slices with their task,
budget, and used tokens.
