# CLI and record reference

## Storage layout

```
.pruner/
  index.sqlite      the repository index (stdlib sqlite3, WAL mode) -- see below
  slices/
    s0001.json       a saved selection result, for `show`/`expand`
    ...
```

A pre-v4 `index.json` store found in the store dir is deleted and rebuilt
as `index.sqlite` automatically (reported as `migrated:` by `index`). The
sqlite store is a cache: a version (`INDEX_VERSION`) or parser change, or
an unreadable/old-schema file, triggers a full rebuild. Every update is one
transaction, so a crashed index run leaves the previous index intact.

## Global options

| flag | meaning |
|---|---|
| `--repo PATH` | repository root (default: cwd) |
| `--store PATH` | store directory (default: `<repo>/.pruner`) |
| `--parser {auto,builtin,tree-sitter}` | symbol extraction backend (default `auto`) |

### Parser backends

- `builtin`: Python via stdlib `ast`, C/C++ via the regex/brace-counting
  parser in `pr_cpp.py`. Always available.
- `tree-sitter` (`pr_treesitter.py`): used when `tree_sitter` plus
  `tree_sitter_python` and `tree_sitter_cpp` are importable (`pip install
  tree-sitter tree-sitter-python tree-sitter-cpp`; never a required
  dependency). Error tolerant (a file with a syntax error still yields the
  symbols it could recover) and real C/C++ syntax; inline C++ methods get a
  `Class::method` qualname. Supported py-tree-sitter versions: 0.22-0.25
  (0.26.0 segfaults on node access and is treated as unavailable). If a
  single file fails to parse with tree-sitter it falls back to builtin for
  that file.
- `auto` picks `tree-sitter` when available, else `builtin`;
  `--parser tree-sitter` without the packages is an error. Switching
  backends (including running `auto` from environments with and without
  the packages) rebuilds the index.

## `pruner index [--force]`

Walks the repo (via `git ls-files` when available, so `.gitignore` is
respected for free; otherwise a hardcoded ignore list — build/vendor/venv/
cache directories, `.pyc`/`.so`/binary-looking files, anything over 2MB),
parses every Python (`.py`) and C/C++ (`.c/.h/.cc/.cpp/.cxx/.hpp/.hh`) file,
and classifies everything else as `config` (CMakeLists.txt, pyproject.toml,
Makefile, `*.yaml`/`.toml`/`.ini`/`.cfg`, ...), `doc` (`.md`/`.rst`/`.txt`),
or `other`. **Incremental**: a file whose (mtime, size) matches the stored
row is not even read; one whose content hash matches is not re-parsed;
only changed files' rows are rewritten. Import resolution reruns for every
file only when the file set changed (a new file can change what an
unchanged file's imports resolve to), else just for re-parsed files.

`select` runs this automatically; run `index` directly only when you want
the report (files parsed vs. reused, symbol/reference counts, parser).

### Store tables (`.pruner/index.sqlite`)

```yaml
meta:    {key, value}        # version, parser, repo_root, indexed_at
files:
  id, path                   # repo-relative posix path
  hash                       # sha256[:16] of the content
  mtime, size                # stat fast path for incremental updates
  kind                       # python | cpp | test | config | doc | other
  language                   # python | cpp | null
  size_tokens, line_count, parse_error
  imports                    # JSON list, module/header names as written
  resolved_imports           # JSON list of in-repo paths they resolved to
symbols:
  id                         # integer row id
  file_id, name, qualname    # qualname dotted for Python nesting, :: for C++
  type                       # function | class | type ('type' = C/C++ struct/class/enum)
  start_line, end_line, doc  # doc: first docstring line, Python only
  tokens                     # chars/4 estimate of the symbol's lines
  parent                     # row id of the enclosing class, if any
  dup                        # 1 for a repeated qualname (overloads, if/else defs)
edges:
  src, name                  # symbol row `src` references `name`
```

Chunk/symbol ids shown everywhere else are strings derived on load:
`"<rel_path>:<qualname>"`, with `"@<start_line>"` appended when `dup`.

Python absolute imports resolve against every **source root**: the repo
root, any `src/` directory, and any directory holding a `pyproject.toml`
/ `setup.cfg` / `setup.py` (plus its `src/`); if several roots provide
the module (monorepo) the one sharing the longest path prefix with the
importer wins. C/C++ includes resolve relative to the including file
(`../a/solver.hpp` normalized), else by path suffix, nearest first.

### Reference resolution (how a name in `edges` becomes a graph edge)

Resolved lazily at query time (`pr_store.Index.resolve/neighbors`), not
stored, so incremental updates stay per-file and common names are never
resolved against every same-named symbol up front. Name-based, not
type-checked -- in priority order: (1) a symbol with that qualname in the
**same file**, (2) a uniquely-named symbol among the files this file's
imports **resolved** to, (3) the only symbol with that name **anywhere** in
the repo (`unique_name_repo_wide`, least trustworthy); otherwise dropped as
`ambiguous`. A symbol's references include calls (`f()`, `obj.f()`),
Python parameter/return annotations, and C/C++ signature identifiers --
deliberately over-inclusive, since names that resolve to nothing cost
nothing. Methods are additionally linked to their class (containment).

## `pruner select --task TEXT [options]`

| flag | meaning |
|---|---|
| `--budget N` | hard token ceiling on `used_tokens` (default 2000) |
| `--changed F [F ...]` | file paths to boost as "recently relevant" |
| `--auto-changed` | use `git diff --name-only HEAD` if `--changed` omitted |
| `--error TEXT` / `--error-file PATH` | compiler error / traceback text; locations parsed from it seed selection directly |
| `--graph PATH` | external `{"edges": [{"from": id, "to": id}]}` JSON to merge into the call graph (additive, never required) |
| `--json` | machine-readable output (also always saved to `.pruner/slices/<id>.json` regardless of this flag) |

Error text may use short relative paths (`solver.cpp:14:`), absolute
paths, or installed-package paths; see scoring.md (component 5) for how
they are matched and disambiguated.

### Output record

```yaml
task:
budget:
used_tokens:
confidence: high | medium | low   # high only if a non-test source chunk landed in required_context; medium if only supporting/tests
hint:        # present when confidence isn't high: verify with grep/reads
notes:       # present when e.g. an error path matched several files (all were kept)

required_context: [chunk, ...]     # score >= 8.0, not a test/config file
supporting_context: [chunk, ...]   # score > 0, below the required threshold
relevant_tests: [chunk, ...]       # selected test chunks (unnamed tests capped at 20% of budget)
relevant_config: [chunk, ...]      # any selected chunk whose file is classified 'config'
omitted_candidates: [chunk, ...]   # scored but not selected (score <= 0, below the relative floor, test cap, or budget); capped at 30, sorted by score desc

slice_id:    # only in the CLI's own output, not part of the scoring record itself
```

Each `chunk`:
```yaml
id: "<rel_path>:<qualname>"   # or "<rel_path>:__file__" (whole file), "<rel_path>:A-B" (error-line window)
file:
start_line: / end_line:
chunk_kind: function | class | class_header | type | file | window | adhoc
score:
reasons: [human-readable strings explaining every scoring component that fired]
tokens: chars/4 estimate; for a selected chunk overlapping earlier ones, only its new lines
is_seed: whether this chunk directly seeded the graph BFS (exact task-name match, or an error-stack location)
```

## `pruner expand <slice_id> [options]`

| flag | meaning |
|---|---|
| `--add ID [ID ...]` | promote specific `omitted_candidates` entries (by `id`, or `file:start-end`) into `supporting_context`; a `file:start-end` matching no candidate is added ad hoc like `--file/--lines` |
| `--file PATH --lines A:B` | inject an arbitrary chunk the scorer never surfaced at all (any file inside the repo, any range) |
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
