---
name: pruner
description: Given a specific task (a bug to fix, a compiler error, a test failure, a change to make), select the smallest sufficient set of repository files/symbols to look at instead of grep-exploring or reading whole files/packages. Supports Python and C/C++. Use before broad repository exploration when the task is concrete enough to name a function, error, or failing test.
---

# pruner

A local, offline repository indexer + relevance scorer. It answers "which
exact file:line ranges should I look at for *this* task", not "how is the
repo connected in general" (that's a full graph tool). Relevance is
lexical/structural (keyword overlap, call-graph distance, error-stack
membership, test relationships) — no embeddings, no LLM calls. See
`references/scoring.md` for exactly how each chunk's score is computed.

## Workflow

1. **Select a slice** for the task at hand:
   ```
   python3 <this-skill-dir>/scripts/pruner.py select --task "<what you're doing>" --budget <N>
   ```
   Add whichever of these apply — they meaningfully sharpen the result:
   - `--error "<compiler error / traceback text>"` or `--error-file <path>` —
     if you have one, always pass it; error-stack membership is the
     strongest relevance signal available.
   - `--changed <file> <file> ...` or `--auto-changed` (uses
     `git diff --name-only HEAD`) — for "review/continue this change" tasks.
   - `--graph <path.json>` — an optional external call/reference graph
     (e.g. from Graphify) to merge in, `{"edges": [{"from": id, "to": id}]}`;
     never required.

   First run indexes the repo (cached under `.pruner/`, incrementally
   re-parsed on later calls — only changed files are re-parsed). Output is
   `required_context` / `supporting_context` / `relevant_tests` /
   `relevant_config`, each with exact `file:start-end` ranges, a score, and
   *why* each chunk was selected. Read those ranges — not whole files, not
   the whole package.

2. **Start from what's returned.** Don't independently grep/explore the
   repo first "just in case" — that's exactly the redundant work this
   tool exists to avoid. If the slice turns out insufficient, that's what
   step 3 is for.

3. **If you hit a missing dependency** (an undefined name, a test that
   needs a fixture you don't have context on, reasoning that stalls
   because something's missing), don't re-run `select` from scratch or
   fall back to open-ended exploration. Check `omitted_candidates` in the
   output first — it lists likely-relevant chunks that were cut for budget
   or score — then:
   ```
   pruner expand <slice_id> --add <chunk_id_from_omitted_candidates>
   pruner expand <slice_id> --file <path> --lines A:B   # anything not listed at all
   ```
   Expansion is targeted (you name exactly what's missing) and still
   budget-enforced by default — pass `--budget-extra N` if you deliberately
   need to go over.

4. `pruner show <slice_id>` re-prints a saved slice cheaply (no re-scoring)
   if you need to recall it later in the same task. `pruner list` shows
   all saved slices.

## Guarantees

- Every returned chunk is an exact `file:start-end` range with a reason.
- The token budget is hard-enforced — `select` never returns more than
  `--budget` tokens of `used_tokens`, and `expand` refuses to exceed it
  unless you explicitly pass `--budget-extra`.
- `omitted_candidates` always lists what was likely relevant but didn't
  make the cut, specifically so progressive expansion has something to
  point at instead of a blind re-explore.
- Indexing is incremental (content-hash based) and fully offline — no
  network calls, no LLM calls, safe to run repeatedly.

## Known scope (MVP)

Python (via `ast`) and C/C++ (via a lightweight regex/state-machine
parser, not libclang) are supported. Everything else (docs, config,
unparsed languages) is still indexed and selectable as whole-file chunks,
just without symbol-level granularity or call-graph edges. See
`references/schema.md` for the full CLI reference and
`references/scoring.md` for what the relevance score does and doesn't
capture.
