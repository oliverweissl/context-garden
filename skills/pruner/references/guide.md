# pruner: operational details

Moved out of `SKILL.md` to keep the on-trigger body small.

pruner answers "which exact file:line ranges should I look at for *this*
task", not "how is the repo connected in general" (that's a full graph tool).

## Extra inputs

- `--graph <path.json>` -- an optional external call/reference graph
  (e.g. from Graphify) to merge in, `{"edges": [{"from": id, "to": id}]}`;
  never required.
- `--parser {auto,builtin,tree-sitter}` (default `auto`).

## Progressive expansion

If you hit a missing dependency (an undefined name, a test that needs a
fixture you don't have context on), don't re-run `select` from scratch or
fall back to open-ended exploration: use `omitted_candidates` and `expand`.

## Guarantees

- Every returned chunk is an exact `file:start-end` range with a reason.
- The token budget is hard-enforced -- `select` never returns more than
  `--budget` tokens of `used_tokens`, and `expand` refuses to exceed it
  unless you explicitly pass `--budget-extra`.
- `omitted_candidates` always lists what was likely relevant but didn't
  make the cut, specifically so progressive expansion has something to
  point at instead of a blind re-explore.
- Indexing is incremental (content-hash based) and fully offline -- no
  network calls, no LLM calls, safe to run repeatedly.

## Known scope

Python (via `ast`) and C/C++ (via a lightweight regex/state-machine
parser, not libclang) are supported. If the optional `tree_sitter`,
`tree_sitter_python` and `tree_sitter_cpp` packages are importable they are
used instead; they are never required. Everything else (docs, config,
unparsed languages) is still indexed and selectable as whole-file chunks,
just without symbol-level granularity or call-graph edges.

## Rules, verbatim

- `--error "<compiler error / traceback text>"` or `--error-file <path>` --
  if you have one, always pass it; error-stack membership is the strongest
  relevance signal available.
