# Validating pruner

pruner's job is: for a concrete task, return exactly the file:line
ranges worth reading, not the whole repo and not an arbitrary grep result
— while never silently exceeding its token budget, and always saying why
each thing was picked (and what was likely relevant but got cut).
Validation checks four things: **indexing correctness** (symbols/edges are
extracted right, including edge cases regex/state-machine parsing tends to
get wrong), **selection precision** (does the output actually match the
spec's three worked examples), **budget enforcement** (hard, no
exceptions without explicit override), and **progressive expansion**
(targeted, cheap, still budget-safe).

## 1. Automated checks (run this first, every time the code changes)

```
bash tests/smoke_test.sh
```

Expected: `ALL CHECKS PASSED`, 26/26 checks, against
`tests/fixtures/sample_repo` (copied to a scratch dir per run — never run
manual `pruner` commands directly inside the fixture directory itself;
use a copy, or clean up `.pruner/` under it afterward if you do). It
exercises:

- **Incremental indexing**: a second `index` run with no file changes
  reuses all 15 files (0 re-parsed).
- **UC1 (bug fix)**: task "Fix incorrect boundary behavior in
  interpolate()." → `interpolate` lands in `required_context`; its two
  callers (`solve_step`, `public_api`) and the `Range` type land in
  `supporting_context`; the boundary tests land in `relevant_tests`;
  `CMakeLists.txt` lands in `relevant_config`; **the entire unrelated
  `csrc/` C++ subsystem is absent from both required and supporting** —
  checked explicitly, not just "present in omitted".
- **UC2 (compiler failure)**: a GCC-style error string pointing at
  `csrc/solver.cpp:14` → the enclosing function (`residual`) is required;
  the referenced type (`Matrix`, found via signature scanning, not a real
  call) and the declaration (`solver.hpp`) are supporting; the unrelated
  Python package is absent from required.
- **UC3 (solver convergence change)**: `converge()` required, its callee
  `residual()` supporting via graph distance, the convergence test pulled
  into `relevant_tests`.
- **Budget enforcement**: `used_tokens <= budget` on every `select`, and
  both `expand` paths (`--add` from omitted candidates, `--file`/`--lines`
  ad hoc) are refused when they'd exceed budget, then succeed once
  `--budget-extra` is passed.
- **Clean error handling**: unknown slice/chunk ids report a one-line
  error, never a raw Python traceback.

If you touch a parser (`pr_python.py`/`pr_cpp.py`) or the scoring formula,
add a fixture-driven assertion here before considering the change done —
this corpus caught five real bugs during development (see §4) purely by
being driven end-to-end rather than unit-tested in isolation.

## 2. Fidelity checks against the spec's acceptance criteria

| Criterion (from spec) | How to check | Status |
|---|---|---|
| Supports Python and C/C++ | `pr_python.py` (stdlib `ast`), `pr_cpp.py` (regex/state-machine); fixture repo has both | met |
| Produces exact file/line ranges | every chunk carries `start_line`/`end_line`; smoke test checks specific ranges | met |
| Enforces a hard token budget | `select` never exceeds `--budget`; `expand` refuses over-budget adds without `--budget-extra` (smoke-tested) | met |
| Includes relevant tests automatically | `relevant_tests` bucket, populated via `test_links` + graph distance; UC1/UC3 smoke checks | met |
| Supports progressive expansion | `pruner expand` (by omitted-candidate id, or ad hoc file:lines); doesn't re-run scoring, targeted only | met |
| Reports why each chunk was selected | every chunk carries a `reasons` list of human-readable strings | met |
| Reports likely relevant omitted chunks | `omitted_candidates`, sorted by score desc, capped at 30 | met |

## 3. Manual behavioral check (does an agent actually explore less)

Automated checks prove the indexer/scorer are correct; they don't prove an
agent will use `select` instead of its own grep/Read exploration. Do this
once per significant `SKILL.md` change:

1. Pick a real multi-file repo (or reuse `tests/fixtures/sample_repo`) and
   a concrete task naming a specific function.
2. **Baseline**: ask an agent (skill not installed) to make the change.
   Count files opened/grepped and approximate repository-context tokens
   consumed before it starts editing.
3. **With skill**: install `pruner/`, repeat the same prompt. Confirm
   it runs `pruner select` early, reads only the returned ranges (not
   the containing whole files), and only reads more when `expand` or a
   fresh `select` is warranted by something the slice didn't cover.
4. Compare repository-context tokens and file-read count. Expect both
   lower, **with equal or better task completion** — per the spec's
   success criterion, a completion-rate regression is not an acceptable
   trade for fewer tokens; if the agent gets it wrong because the slice
   omitted something it needed and didn't think to `expand`, that's a
   scoring gap to fix (weak lexical seed, missing graph edge), not a
   result to accept.

## 4. Known limitations (accepted for MVP, revisit if they cause failures)

- **Purely lexical/structural relevance, no semantic understanding** — a
  task described in vocabulary absent from the code will seed poorly.
  `--error`/`--changed` don't have this problem; encourage using them
  whenever available (SKILL.md already does).
- **C/C++ parsing is a lightweight heuristic**, not a real parser — see
  `pr_cpp.py`'s module docstring and `references/scoring.md`'s
  limitations section for specifics (no macro/template understanding, no
  string/comment-aware brace counting).
- **Call-graph edges are name-resolved, not type-checked** — see
  `precision` field on `call_edges` (`same_file` > `resolved_import` >
  `unique_name_repo_wide` > dropped as `ambiguous`); the scorer doesn't
  currently weight by precision, so a same-file resolution and a
  unique-name-repo-wide guess count equally toward graph distance.
- **Greedy-by-score selection, not exact knapsack** — see
  `references/scoring.md` for why, and when this could under-pack a
  budget compared to a density-based ordering.
- **`expand`'s `--add` only knows about that slice's `omitted_candidates`**
  (capped at 30) — a chunk scored below the cutoff shown isn't
  promotable by id; use `--file`/`--lines` for anything not listed.

## 5. Bugs this fixture corpus caught during development

Listed because they're exactly the kind of thing a narrower, unit-level
test suite would likely have missed by testing each parser in isolation
with hand-picked inputs, rather than end-to-end against a real (if small)
multi-file, multi-language repo:

1. A Python class symbol's `calls` list wrongly included every one of its
   methods' calls too (nested-def calls leaking into the parent).
2. The C++ parser matched a function's own name in its signature as a
   spurious self-call on *every* function (`clamp` "calling" `clamp`).
3. One-liner C++ function bodies (`int getX() { return 42; }`) never
   closed in the brace-depth state machine, and separately lost all their
   calls once the self-call fix (#2) was applied too broadly.
4. Multi-line C++ signatures reported `start_line` as the line containing
   `{`, not the true start of the signature (`buffer_start_line` was read
   after being reset to `None`).
5. The filename-convention test-link heuristic (`test_foo.py` <-> `foo.py`)
   ignored language, cross-linking a Python test to an unrelated C++ file
   with the same stem (`test_solver.py`/`test_solver.cpp` both matching
   both `solver.py` and `solver.cpp`).

All five are covered by explicit smoke-test assertions: #1-#4 by the
standalone parser regression check at the top of `tests/smoke_test.sh`
(`pr_cpp`/`pr_python` called directly against small inline snippets), #5 by
the UC1/UC2 end-to-end assertions against the fixture repo. Regressions
here should fail loudly, not silently degrade selection quality.
