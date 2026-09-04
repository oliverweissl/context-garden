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

For each candidate chunk (one per symbol, plus one whole-file chunk for
files with no symbols):

1. **Lexical match** (`lexical_match`): the task text is lowercased and
   keyword-extracted (split on non-identifier chars, further split
   `snake_case`/`camelCase` into sub-words, stopwords removed). A chunk's
   symbol name, docstring, and file path are compared against it:
   - the task text directly names the symbol (word-boundary substring
     match, e.g. task mentions `interpolate()` and there's a symbol named
     `interpolate`) → **+10**, and this is what makes a chunk a graph BFS
     **seed**
   - otherwise, shared keywords between the *symbol name's* sub-words and
     the task → **+3 per shared word**
   - shared keywords with the **docstring** → **+1 per shared word**
   - shared keywords with the **file path** → **+0.5 per shared word**

2. **Graph distance** (`graph_score`, over `pr_graph.py`'s BFS): starting
   from the seed set (exact-name matches, plus any symbol enclosing an
   `--error`-parsed location, falling back to `--changed`-file symbols if
   neither produced a seed), BFS over the call/reference graph (undirected
   — a *caller* of a seed is exactly as close as a *callee*, which is
   what surfaces UC1's "two callers"). Score is `max(0, 6 - distance)`,
   i.e. 5 at one hop, 4 at two hops, down to 0 at six-plus. Symbols the
   call graph never connects to a seed fall back to a fixed same-file
   (distance 2) or import-linked (distance 3) score, so e.g. a helper
   sitting right next to a seed in the same file still gets *some* credit
   even with zero direct call edges between them.

3. **Test relationship** (`+8`): a chunk classified `test` whose file
   (via `test_links`, built from resolved imports + the `test_foo.py` <->
   `foo.py` filename convention) targets a seed's file.

4. **Recency** (`+6`): the chunk's file appears in `--changed` /
   `--auto-changed`.

5. **Error-stack membership** (`+15`): the chunk's line range contains a
   location parsed out of `--error`/`--error-file` text (GCC-style
   `file:line:col: error:` or a Python `File "...", line N` traceback
   frame). This is the strongest signal available — an error message
   telling you exactly where the problem is beats every heuristic above.

6. **Primary config bonus** (`+0.5`, flat): `CMakeLists.txt`,
   `pyproject.toml`, `setup.py`, `Makefile`, `package.json` always get a
   small positive score if nothing else touched them, so a task's
   `relevant_config` bucket isn't empty just because the task text never
   mentions the build system — these are cheap and close to universally
   useful (see the UC1 worked example in `validate.md`, which expects a
   plain bug fix to still surface `CMakeLists.txt`/`pyproject.toml`).

Total score is the sum. There is no normalization across components by
design — a direct error-stack hit or exact name match should dominate a
handful of weak keyword overlaps, and the fixed weights above make that
happen predictably rather than through a tuned/learned combination.

## Selection: greedy by score, not exact knapsack

`select()` sorts all chunks with score > 0 by score descending and adds
them to the slice while `used_tokens + chunk_tokens <= budget`; anything
that doesn't fit or scores ≤0 goes to `omitted_candidates`. This is an
approximation of the "maximize expected usefulness subject to budget"
knapsack the spec describes, not an exact solve (a density-based
`score/token` ordering would pack more total score into a fixed budget in
some cases). Greedy-by-score was chosen over knapsack optimality because:

- it's trivially explainable ("included because it scored higher than
  everything that got cut"), which matters since every chunk's presence
  has to be justified in `reasons`;
- the score differences that matter most (an error-stack hit at +15 vs.
  a weak keyword overlap at +1) are large enough that density-ordering
  would rarely change the outcome for the highest-value chunks anyway;
- it keeps the codebase small — see the project-wide preference for
  simple implementations over marginally-better-but-opaque ones.

`required_context` vs. `supporting_context` is just a score threshold
(`REQUIRED_THRESHOLD = 8.0` in `pr_select.py`) applied after the
test/config buckets are pulled out — it is not a separate selection pass.

## Known limitations

- **Lexical matching is vocabulary-bound.** A task described entirely in
  words absent from the code (different terminology, a translated
  description, an analogy) will produce zero lexical seeds and fall back
  to `--changed`-file seeding or, absent that, an empty/low-confidence
  slice. Passing `--error`/`--changed` when available sidesteps this —
  they don't depend on wording at all.
- **C/C++ parsing is regex/state-machine based, not a real parser** (see
  `pr_cpp.py`'s docstring): no macro expansion, no template
  specialization understanding, no awareness of braces inside string/char
  literals or comments (which can throw off a function's detected end
  line in adversarial or unusually-commented code). Good enough for
  finding "the enclosing function" and "the referenced type", not for
  anything requiring real semantic C++ understanding.
- **Call-graph resolution is name-based**, not type-checked — two
  same-named methods on unrelated classes can be confused
  (`precision: unique_name_repo_wide`/`ambiguous` in `call_edges` flags
  exactly this uncertainty, but the scorer currently treats all resolved
  edges the same regardless of `precision`).
- **No true semantic/embedding similarity** — this is a permanent scope
  boundary, not a gap to fill in later, consistent with every other skill
  in this suite avoiding model calls for deterministic bookkeeping.
