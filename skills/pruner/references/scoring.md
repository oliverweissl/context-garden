# Relevance scoring

No embeddings, no LLM calls anywhere in pruner — "semantic relevance"
from the spec is approximated by keyword overlap and structural graph
distance. This is a deliberate scope decision consistent with the other
skills in this suite (compost, seedbank): local, offline,
deterministic, fully explainable. It will miss genuinely semantic
connections that share no vocabulary (a task that says "smooth the curve"
won't find a function called `interpolate` unless something nearby shares
a word) — see Limitations below.

## Components (implemented in `pr_score.py` / `pr_select.py`)

For each candidate chunk (one per symbol, a `class_header` chunk for
large classes, plus one whole-file chunk for files with no symbols):

1. **Lexical match** (`lexical_match`): the task text (plus identifiers
   the error names -- see 5) is keyword-extracted (split on non-identifier
   chars, `snake_case`/`camelCase` split into sub-words, stopwords removed,
   a tiny suffix stemmer so `clamped` meets `clamp`). Every word is weighted
   by its **IDF** over all chunk names in the index, normalized to (0, 1]:
   a word unique to one symbol weighs ~1, `get`/`run`/`convert` weigh
   little.
   - task names `Owner.name` / `Owner::name` exactly → **+12**
   - task directly names the symbol → **+12 x idf** (one-word names use the
     smaller of whole-name and word IDF). Case-insensitive-only matches
     count half; a name that is a stopword (`fix`, `update`) a quarter; a
     name mentioned only as `Other.name` for a *different* owner a quarter
   - otherwise shared name sub-words → **+3 x idf** per word
   - the enclosing class/module is named in the task → **+4 x idf**
   - docstring overlap → **+1 x idf** per word; file stem overlap → +0.5
     per word (not for whole-file chunks, whose name *is* the file)

2. **Graph distance** (`graph_score`, over `pr_graph.bfs_distances`):
   BFS from the **seed set** over the undirected call/reference graph
   (resolved lazily from the store, see schema.md) plus class <-> method
   containment links, up to 5 hops. Seeds are chunks scoring >= 9
   lexically (a rare exact name or a qualified name -- *not* any
   single-word overlap) plus error-stack hits; failing that, the top <= 3
   lexical source chunks scoring >= 4 (and >= 60% of the best); failing
   that, the `--changed` files' symbols. A seed scores **+6**; others
   `6 - distance` (5 at one hop ... 1 at five), **+0.5** extra for what a
   seed calls/uses directly (callee over caller). Reverse lookups on names
   referenced from > 200 places are restricted to the target's file and
   its importers, so hubs like `get` don't flood the BFS.
   Symbol-less source files (declaration-only headers, re-export modules)
   linked by include/import to a seed's file score **5** if they mention a
   seed's name (e.g. the header declaring the function an "undeclared
   identifier" error is about), else 1. There is no flat "any symbol in an
   import-linked file" score any more.

3. **Test relationship** (`+8`): a test chunk whose file targets a seed's
   file (resolved imports + the `test_foo.py` <-> `foo.py` convention).

4. **Recency** (`+6`): the chunk's file appears in `--changed` /
   `--auto-changed`.

5. **Error-stack membership** (`+15`): the chunk's line range contains a
   location parsed from `--error`/`--error-file` (gcc/clang
   `file:line:col: error|warning|note|fatal error:` / `required from`,
   MSVC `file(line): error`, Python `File "...", line N`, pytest
   `path.py:N:`). A location hitting no symbol selects a +/-15 line
   `window` (or the whole small file). Paths are matched by longest
   common path suffix, so `solver.cpp`, `/abs/repo/x/y.py` and installed
   paths (`.../site-packages/pkg/mod.py` -> `<root>/src/pkg/mod.py` when
   `<root>/src` is a Python source root) all resolve. When a short path
   matches several files (`a/solver.cpp`, `b/solver.cpp`) it is narrowed
   by build-dir hints in the error text (`make: Entering directory`,
   `cd X &&`, `In file included from`), then by which candidate's symbols
   (or the enclosing symbol's references) match other identifiers in the
   error; if still ambiguous all are kept and a `notes` entry says so.
   Identifiers the error names -- exception types, `FAILED path::test`,
   quoted names, frame functions, and identifiers in code echoed back by
   the tool (pytest `>` lines, gcc `NN | code` lines) -- join the task
   text for lexical scoring.

6. **Nearest project config** (`+0.5`): for the selected source files
   (seeds / chunks >= 8, else the top 3), walk up to the first directory
   holding a primary config (`CMakeLists.txt`, `pyproject.toml`,
   `setup.py`, `setup.cfg`, `Makefile`, `package.json`); those configs get
   the bonus. Configs under `fixtures/`/`testdata/` directories never
   count (and score 0 unless an error points into them), so in a monorepo
   only the owning project's config is suggested.

Total score is the sum. There is no normalization across components by
design -- a direct error-stack hit or exact rare name should dominate a
handful of weak keyword overlaps.

## Selection: greedy by score, not exact knapsack

`select()` walks chunks in descending score order and adds each while
`used_tokens + cost <= budget`, with these rules:

- **Relative floor**: chunks scoring below 15% of the top non-config
  score are omitted (listed in `omitted_candidates`), so a generous budget
  isn't filled with filler.
- **Source first**: test chunks the task/error doesn't name (by test
  function name, or because an error location is inside them) share a cap
  of 20% of the budget (10% when the error names specific failing tests).
  Named/failing tests are uncapped.
- **No double counting**: tokens are accounted per unique line. A chunk
  overlapping already-selected lines costs only its new lines; one fully
  covered already is skipped. Classes longer than 30 lines that contain
  methods are represented by a `class_header` chunk (class line,
  docstring/fields, first-method signature -- at most 20 lines) instead of
  the whole body, so the class and its methods don't compete for the same
  tokens.

This approximates the "maximize usefulness subject to budget" knapsack
rather than solving it; greedy-by-score stays explainable ("included
because it scored higher than everything that got cut").

`required_context` vs. `supporting_context` is a score threshold
(`REQUIRED_THRESHOLD = 8.0` in `pr_select.py`) applied after the
test/config buckets are pulled out.

## Evaluation

`tests/eval/run_eval.py` (run by `tests/smoke_test.sh`) scores 22
labelled cases (task and/or error text + ground-truth `file:start-end`
ranges) on the committed fixtures `tests/fixtures/sample_repo` and
`tests/fixtures/mono_repo` (src-layout package, second pyproject, C++
backends with duplicate basenames). It reports recall at the case's
budget, precision (ground-truth share of selected tokens) and recall at a
tight budget (~1.3x the ground truth's tokens), and fails if recall drops
below `tests/eval/baseline.json`. Any scoring change should be validated
there first; `PRUNER_EVAL_SCRIPT=<other pruner.py>` compares against
another checkout.

## Known limitations

- **Lexical matching is vocabulary-bound.** A task described entirely in
  words absent from the code (different terminology, a translated
  description, an analogy) will produce zero lexical seeds and fall back
  to `--changed`-file seeding or, absent that, an empty/low-confidence
  slice. Passing `--error`/`--changed` when available sidesteps this —
  they don't depend on wording at all.
- **The builtin C/C++ parser is regex/state-machine based, not a real
  parser** (see `pr_cpp.py`'s docstring; install the optional tree-sitter
  backend for real syntax, see schema.md): no macro expansion, no template
  specialization understanding (string/char literals and comments are
  blanked before brace counting, but macros that open/close braces can
  still throw off a function's detected end line). Good enough for
  finding "the enclosing function" and "the referenced type", not for
  anything requiring real semantic C++ understanding.
- **Call-graph resolution is name-based**, not type-checked — two
  same-named methods on unrelated classes can be confused
  (`Index.resolve` returns a precision -- `same_file`, `resolved_import`,
  `unique_name_repo_wide`, `ambiguous` -- but the scorer currently treats
  all resolved edges the same).
- **String literals are not indexed**: an error message text such as
  "No such command" won't find the function that raises it unless its
  name/doc shares words with the task.
- **No true semantic/embedding similarity** — this is a permanent scope
  boundary, not a gap to fill in later, consistent with every other skill
  in this suite avoiding model calls for deterministic bookkeeping.
