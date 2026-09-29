# weeder: operational details

Moved out of `SKILL.md` to keep the on-trigger body small.

## Rules, verbatim

- Tokens are reported as always-loaded (description) and on-trigger (body)
  separately, labelled as estimates (chars/4 unless calibrated -- see
  `classification.md`).
- The `optimize` pass is lossless (every word survives, just relocated) and
  reversible, so it's safe to always apply -- it never needs judgment.
- **Remove genuinely obvious/general instructions** flagged under "likely
  unnecessary content" -- but only ones that are actually obvious to a
  capable agent; don't cut something just because it's short.
- How much of the judgment step gets structured for you as an explicit
  request/answer file, vs. you editing the copy directly, is controlled by
  this repo's LLM-assist level (`none` default, `slight`, `lot` -- resolved
  from `--assist`, `$CONTEXT_GARDEN_LLM_ASSIST`, or
  `.context-garden/config.yaml`'s `llm_assist.level`; see `llm-assist.md`
  for what each level structures and why this never involves a second
  model call). At `none`, `suggest` reports zero requests -- do step 3
  freehand. `apply-suggestion` only applies the answer -- it doesn't
  validate it.
- `diff` prints one before/after report: token breakdown (always-loaded
  description vs. on-trigger body), SKILL.md reduction %, routing accuracy
  before/after, constraint-preservation ratio.
- Without any `--competing` skill, almost any nonzero keyword overlap
  "wins" trivially in `test-routing`, which tells you much less.
