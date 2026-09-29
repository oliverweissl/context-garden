# LLM-assist levels for step 3

`weeder` never makes a network call or needs an API key, at any level.
The level changes how much of step 3 `suggest` packages as a
request/answer file pair for the agent already running this CLI; it never
changes who makes the call or introduces a second model into the loop.

## The three levels

| level | `suggest` produces | rest of step 3 |
|---|---|---|
| `none` (default) | zero requests | do all of step 3 freehand |
| `slight` | one `description_shorten` request | duplicate consolidation and filler removal still freehand |
| `lot` | `description_shorten` + `duplicate_consolidation` + `unnecessary_removal` | nothing left freehand |

Whatever the level, steps 4-7 run **unchanged** against the result — an
assist-drafted answer is held to the same bar as a freehand edit.

## Resolving the level

Precedence, highest first: `--assist {none,slight,lot}` >
`$CONTEXT_GARDEN_LLM_ASSIST` > `llm_assist.level` in the nearest
`.context-garden/config.yaml` (walking up from the cwd) > `none`. The
config parser only reads flat `key: value` lines plus one level of
indented nesting (`llm_assist:` / `  level: slight`).

## Traps

- `--dup-threshold` must match between `suggest` and `apply-suggestion`
  if you use a non-default value — `apply-suggestion` recomputes the
  duplicate grouping from the original `skill_dir` to resolve
  `group_index`/`member_index`.
- `apply-suggestion` only performs mechanical edits; it does not validate
  them. Matching is whitespace-normalized but not code-aware: a filler
  sentence containing inline code (stripped during detection) may fail
  to match verbatim and is reported as a warning and skipped.

## Why consolidating a multi-way duplicate rarely hits 100% first try

`test-function` checks each **original** constraint sentence against the
whole after-document, so collapsing several differently-worded
restatements into one canonical wording drops each variant's distinctive
words and gets them flagged MISSING — the gate working as intended.
Iterate `canonical_text` until the specifics that matter are kept.
