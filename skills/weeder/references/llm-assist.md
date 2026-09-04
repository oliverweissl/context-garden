# LLM-assist levels for step 3

`weeder` never makes a network call or needs an API key, at any level.
"LLM-assist" here means: how much of SKILL.md workflow step 3 (shortening
the description, consolidating duplicate rules, removing obvious filler —
all explicitly *not* automated, see `classification.md`'s "Why the
rewriting itself isn't automated") is packaged as a structured
request/answer file pair for the agent already running this CLI, instead
of that agent reading `audit`'s raw output and hand-editing the file. The
level changes how much of that judgment call is structured; it never
changes who makes the call or introduces a second model into the loop.

## The three levels

| level | `suggest` produces | rest of step 3 |
|---|---|---|
| `none` (default) | zero requests | do all of step 3 freehand, as before this feature existed |
| `slight` | one `description_shorten` request | duplicate consolidation and filler removal still freehand |
| `lot` | `description_shorten` + `duplicate_consolidation` + `unnecessary_removal` | nothing left freehand |

Whatever the level, steps 4-7 of SKILL.md's workflow (`test-routing`,
`test-function`, `diff`, and the accept-only-if rule) run **unchanged**
against the result — an assist-drafted answer gets no free pass; it's
held to exactly the same bar as a freehand edit.

## Resolving the level

Precedence, highest first:

1. `--assist {none,slight,lot}` passed directly to `weeder suggest`.
2. The `CONTEXT_GARDEN_LLM_ASSIST` environment variable.
3. `llm_assist.level` in the nearest `.context-garden/config.yaml`, found
   by walking up from the current working directory:
   ```yaml
   llm_assist:
     level: slight
   ```
4. `none`, if nothing above sets it.

`.context-garden/config.yaml` is repo-local, committed config (see
`docs/architecture.md`) — set it once per repo instead of passing
`--assist` on every invocation. The parser only understands flat
`key: value` lines and one level of indented nesting (exactly the shape
above); it is not a general YAML parser, to avoid adding a PyYAML
dependency to an otherwise pure-stdlib skill.

## `suggest`'s request shapes

```
weeder suggest <skill_dir> [--assist LEVEL] [--dup-threshold 0.6] [--json]
```

- **`description_shorten`**: `current_description`, `current_tokens`,
  `instructions`.
- **`duplicate_consolidation`**: `groups`, each `{group_index,
  max_similarity, members: [{member_index, text, source}, ...]}` — lifted
  straight from `weeder audit`'s `duplicate_groups`. `member_index` (not
  `source`) identifies a member unambiguously, since two members can share
  the same `source` label (e.g. two duplicate sentences in the same
  section).
- **`unnecessary_removal`**: `items`, each `{reason, heading, text}` —
  lifted from `audit`'s `likely_unnecessary`, filtered to the
  filler-phrase entries (the movable-section entries are already handled
  mechanically by `optimize`, not by this).

`--dup-threshold` must match whatever you pass to `apply-suggestion` later
if you use a non-default value — it determines `duplicate_groups`'
indexing, and `apply-suggestion` recomputes that grouping from the
original `skill_dir` to apply `group_index`/`member_index`.

## Answering and applying

Write a JSON file with any subset of these keys, then run:

```
weeder apply-suggestion <skill_dir> --answer <answer.json> [--out <dir>] [--dup-threshold 0.6]
```

```json
{
  "description_shorten": {"new_description": "..."},
  "duplicate_consolidation": [
    {"group_index": 0, "keep_member_index": 1, "canonical_text": "..."}
  ],
  "unnecessary_removal": [
    {"heading": "...", "text_to_remove": "..."}
  ]
}
```

- `description_shorten` rewrites the frontmatter `description:` line.
- `duplicate_consolidation` rewrites the member at `keep_member_index` to
  `canonical_text` **in place**, and deletes every other member's text
  entirely from its own location — "state it once, delete the rest," not
  "repeat the same wording everywhere" (the latter would turn a
  near-duplicate into an exact one). Omit a group you'd rather leave as-is.
- `unnecessary_removal` deletes each named text verbatim (whitespace-
  normalized, so a sentence that had its internal newlines flattened
  during detection still matches).

`apply-suggestion` only performs these mechanical edits — it does not
validate them. **Known limitation**: matching is whitespace-normalized but
not code-aware; a duplicate member or filler sentence containing inline
code/backticks (stripped for detection purposes, see `we_dupes.py`) may
fail to match verbatim in the real file and gets reported as a warning
rather than silently skipped or partially applied.

## Why consolidating a real multi-way duplicate rarely hits 100% on the first try

`test-function`'s coverage check runs per **original** constraint sentence
against the **entire** after-document — not just the location a
consolidated rule now lives in. Four independently-worded restatements of
"never delete without confirmation" each carry some distinctive words of
their own (one mentions audit purposes, another says work "cannot be
recovered," a third calls the file "temporary"); collapsing them into one
canonical wording necessarily drops most of those distinctive words from
the document entirely, which `test-function` will correctly flag as
missing coverage for the deleted variants. That's the gate doing its job,
not a bug in this feature — see `references/routing-heuristic.md`'s
"`test-function`: constraint-coverage proxy" section for the same
principle applied to freehand edits. Iterate the `canonical_text` (or
`--min-ratio`-aware judgment about which variant's specifics actually
matter) the same way you would for a manual consolidation.
