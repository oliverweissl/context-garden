# Structural classification and measurement

## How sections are classified (`we_parse.py`)

`SKILL.md` is split on H1/H2 headings only (H3+ nests inside its parent
section). Each section is classified by a **heading-keyword** match,
checked in this order:

| category | heading matches |
|---|---|
| `examples` | `example`/`examples` |
| `background` | `background`, `motivation`, `why`, `rationale`, `history` |
| `external_reference` | `reference(s)`, `see also`, `further reading`, `link(s)` |
| `constraints` | `constraint(s)`, `rule(s)`, `invariant(s)`, `guarantee(s)`, `safety`, `limitation(s)` |
| `workflow` | `workflow`, `step(s)`, `process`, `usage`, `how to`, `getting started` |
| `mandatory_instructions` | (default — no heading, or heading matches none of the above) |

The frontmatter `description:` field is always `routing_metadata`,
counted separately.

**This is a heading-keyword heuristic, not content understanding.** A
section titled "Background" that's actually load-bearing instructions
gets classified `background` anyway (and flagged as movable — check
`audit`'s output before trusting `optimize` on an unfamiliar skill). A
section titled "Notes" with genuinely optional commentary stays
`mandatory_instructions` and won't be flagged. When a heading doesn't
clearly signal its content, retitle it.

## Token accounting (`we_audit.py`)

Claude Code keeps only each skill's `name` + `description` in context
every session; the `SKILL.md` body loads in full only when the skill
triggers, and `references/*.md` only when explicitly read. `audit`
reports these separately:

- `always_loaded_tokens` = the description only.
- `on_trigger_body_tokens` = the **entire** `SKILL.md` body, regardless
  of how any section is classified.
- `skill_md_tokens` = the two combined — what `optimize`/`diff` report a
  reduction of.

`movable_tokens` is the subset of the body classified
`background`/`examples`/`external_reference` — tokens that *could* become
on-demand if moved, but haven't been yet. Only content actually relocated
to `references/*.md` counts as on-demand (`reference_tokens`).

## Duplication detection (`we_dupes.py`)

Two passes, both Jaccard similarity on normalized word sets
(deterministic, no LLM; fenced code blocks and headings are stripped
first):

- **Paragraph-level** (`threshold=0.6`, `MIN_WORDS=8`): large repeated
  blocks.
- **Sentence-level** (`threshold=0.45`, `MIN_SENTENCE_WORDS=6`), OR'd with
  a contiguous-word-run check (`MIN_SHARED_PHRASE_WORDS=6`): the same
  rule restated with *different* surrounding elaboration each time. Two
  sentences sharing an unbroken 6+-word run are flagged even if their
  overall Jaccard is low, since a different trailing clause on each
  occurrence dilutes whole-sentence similarity below threshold.

**Known miss case**: a near-duplicate that differs by one word in a way
that breaks the contiguous run (e.g. "never delete a file" vs. "never
delete a **data** file") can still evade both signals if the resulting
fragments are each individually short. 6 is a balance, not a proven
optimum — lowering `MIN_SHARED_PHRASE_WORDS` catches more at the cost of
more false positives.

## "Likely unnecessary content"

Two mechanical flags, not general "unnecessariness" detection:

- **Movable sections**: any `background`/`examples` section.
- **Generic filler phrasing** (`we_filler.py`): a sentence is flagged
  only if it is (a) **not an instruction** — contains none of
  must/never/always/do not/don't/only/should/required/ensure/run/use/
  call/pass/set/avoid/prefer and does not open with a base-form verb —
  and (b) matches a hedge / pleasantry / rationale-only pattern ("This is
  useful because...", "Feel free to...", "this skill is designed
  to..."). Biased toward missing filler rather than flagging a real
  instruction: unknown first words count as verbs (not flagged), and a
  pleasantry that carries a constraint ("Feel free to skip step 3 only
  when...") is never flagged.

## Token estimates

Every token count weeder prints is an estimate, labelled as such in
`audit`/`optimize`/`diff` output (`token_estimate` in `--json`):

- **uncalibrated chars/4** — the suite-wide default heuristic.
- **calibrated YYYY-MM-DD** — when `references/token-calibration.json`
  exists (written by the context-garden repo's `calibrate-tokens`
  script, which the user runs manually against their own `claude` CLI;
  weeder only reads the file, it never makes a call). Measured
  chars-per-token ratios are applied per content category: `yaml` for
  the description, `code` for fenced code blocks, `markdown` for the
  rest; the report shows the calibration's observed per-sample error
  range. `$WEEDER_TOKEN_CALIBRATION` overrides the file path.

## What is not automated

`optimize` only performs moves that are lossless by construction
(relocate text verbatim, leave a pointer); shortening the description,
merging duplicates and removing filler are judgment calls, structured
optionally via `suggest`/`apply-suggestion` (see `llm-assist.md`).
