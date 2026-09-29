---
name: pruner
description: Pick the smallest set of file:line ranges relevant to a concrete task (bug, compiler error, failing test, change) in a large Python or C/C++ repository, instead of grep-exploring or reading whole files. Skip when the task or traceback already names the file.
---

# pruner

Offline indexer + lexical/structural relevance scorer (keyword overlap,
call graph, error-stack membership, test relationships; no embeddings,
no LLM). How scores work: `references/scoring.md`.

`pruner` is not on PATH: run `<this-skill-dir>/bin/pruner`.

1. Select a slice:
   ```
   <this-skill-dir>/bin/pruner select --task "<what you're doing>" --budget <N> \
     [--error "<traceback>" | --error-file <path>] [--changed <files...> | --auto-changed]
   ```
   Always pass `--error` when you have one; it is the strongest signal.
   First run builds a SQLite index under `.pruner/`; later runs re-parse
   only changed files. Output lists `required_context`,
   `supporting_context`, `relevant_tests`, `relevant_config` as exact
   `file:start-end` ranges with a reason each. Read those ranges, not whole
   files.
2. The slice is a starting point, not a fence. If confidence is
   `low`/`medium` (a `hint:` line is printed) or the slice doesn't explain
   the behavior, fall back to targeted grep and reads.
3. Missing a dependency? Check `omitted_candidates` first, then
   `bin/pruner expand <slice_id> --add <id or file:start-end>` (or
   `--file <path> --lines A:B`) instead of re-running `select`. The budget
   stays enforced unless you pass `--budget-extra N`.
4. `bin/pruner show <slice_id>` / `list` re-print saved slices without re-scoring.

Details (guarantees, `--graph`, tree-sitter backend, language scope):
`references/guide.md`; full CLI: `references/schema.md`.
