# Relevance scoring

No embeddings or LLM calls: relevance is keyword overlap plus call-graph
distance. A chunk's score is the plain sum of the components below
(`pr_score.py` / `pr_select.py`); each one that fired appears in its
`reasons`.

## Signals

| signal | score |
|---|---|
| task names `Owner.name` / `Owner::name` exactly | +12 |
| task names the symbol | +12 x idf (case-insensitive-only x0.5; stopword name or other owner's `Other.name` x0.25) |
| shared name sub-word | +3 x idf per word |
| enclosing class/module named | +4 x idf |
| docstring word / file-stem word | +1 x idf / +0.5 |
| graph: seed / N hops (N <= 5) | +6 / 6 - N; +0.5 extra for a seed's direct callee |
| symbol-less file include/import-linked to a seed's file | 5 if it mentions a seed's name, else 1 |
| test targeting a seed's file (imports or `test_foo.py` <-> `foo.py`) | +8 |
| file in `--changed` / `--auto-changed` | +6 |
| line range contains an `--error` location | +15 |
| nearest project config of selected sources | +0.5 |

idf is normalized to (0, 1] over chunk names: a word unique to one symbol
weighs ~1, `get`/`run` weigh little. Keywords split `snake_case`/
`camelCase`, drop stopwords, and stem (`clamped` meets `clamp`).

**Seeds**: lexical score >= 9 (`SEED_THRESHOLD`) plus error-stack hits;
else the top <= 3 source chunks scoring >= 4 and >= 60% of the best;
else the `--changed` files' symbols. BFS depth `MAX_DEPTH = 5`; names
referenced from > 200 places (`CALLER_FANOUT_CAP`) only reverse-resolve
within the target's file and its importers.

**Errors**: locations parse from gcc/clang `file:line:col:` (incl.
`required from`), MSVC `file(line):`, Python `File "...", line N` and
pytest `path.py:N:`; one hitting no symbol selects a +/-15 line
`window`. Paths match by longest common suffix; a short path matching
several files is narrowed by build-dir hints and identifiers in the
error, else all are kept with a `notes` entry. Identifiers the error
names (exception types, failing tests, quoted names, echoed code) join
the task text.

## Selection (greedy by score)

- `required_context`: score >= 8.0 (`REQUIRED_THRESHOLD`).
- Chunks below 15% of the top non-config score are omitted
  (`MIN_RELATIVE_SCORE = 0.15`).
- Tests the task/error doesn't name share 20% of the budget (10% when the
  error names failing tests); named tests are uncapped.
- Tokens count per unique line; overlapping chunks cost only new lines.
- Classes > 30 lines with methods become a `class_header` chunk (<= 20
  lines).
- `omitted_candidates` is capped at 30 (`OMITTED_CAP`).

## Evaluation

22 labelled cases in `tests/eval/cases.json`; `tests/eval/run_eval.py`
(run by `tests/smoke_test.sh`) fails if recall drops below
`tests/eval/baseline.json`. Validate any scoring change there.

## Known limitations

- **Lexical matching is vocabulary-bound**: a task worded differently
  from the code yields no lexical seeds. Pass `--error`/`--changed` when
  available; they don't depend on wording.
- **Builtin C/C++ parser is regex-based**: no macro expansion; macros
  that open/close braces can mis-size a function's end line. The optional tree-sitter backend parses real
  syntax (see schema.md).
- **Call-graph resolution is name-based**, not type-checked: same-named
  methods on unrelated classes can be confused.
- **String literals are not indexed**: an error message like "No such
  command" won't find the function raising it unless its name/doc shares
  words with the task.
