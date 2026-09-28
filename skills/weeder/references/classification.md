# Structural classification and measurement

## How sections are classified (`we_parse.py`)

`SKILL.md` is split on H1/H2 headings only (H3+ nests inside its parent
section's content — fine granularity isn't needed for token accounting or
progressive-disclosure moves at this scale). Each section is classified
by a **heading-keyword** match, checked in this order:

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
section titled "Notes" with genuinely optional color commentary stays
`mandatory_instructions` (default) and won't be flagged. When a heading
doesn't clearly signal its content, retitle it — that's also just good
skill-authoring practice independent of this tool.

## Token accounting (`we_audit.py`)

Claude Code keeps only each skill's `name` + `description` in context
every session; the `SKILL.md` body loads (in full) only when the skill
triggers, and `references/*.md` only when explicitly read. `audit`
reports these separately:

- `always_loaded_tokens` = the description only ("always-loaded
  (description)").
- `on_trigger_body_tokens` = the **entire** `SKILL.md` body (every
  section), because the whole body loads whenever the skill triggers,
  regardless of how any individual section is classified ("on-trigger
  (body)").
- `skill_md_tokens` = the two combined -- what `optimize`/`diff` report a
  reduction of.

`movable_tokens` is the subset of the body currently classified
`background`/`examples`/`external_reference` — tokens that *could* become
genuinely on-demand if moved to a reference file, but haven't been yet.
Only content that has actually been relocated to `references/*.md` counts
as on-demand (`reference_tokens`). Conflating "classified as movable"
with "already on-demand" was a real bug caught during this tool's own
development — `optimize`'s before/after reduction
number is meaningless if it's computed that way, since a skill with a
huge unmoved Background section would report the same on-trigger
figure before AND after actually moving it.

## Duplication detection (`we_dupes.py`)

Two passes, both Jaccard-similarity based (normalized word sets,
deterministic, no LLM):

- **Paragraph-level** (`threshold=0.6`, `MIN_WORDS=8`): catches large
  repeated blocks — the same multi-sentence explanation copy-pasted
  somewhere else.
- **Sentence-level** (`threshold=0.45`, `MIN_WORDS=6`), **plus** a
  contiguous-word-run check (`MIN_SHARED_PHRASE_WORDS=6`): catches spec's
  UC2 shape specifically — the same rule restated with *different*
  surrounding justification/elaboration each time. Whole-sentence Jaccard
  alone under-catches this (a shared 8-word imperative core gets diluted
  below threshold once each occurrence has a different trailing clause
  tacked on), which is why the contiguous-run signal exists: two
  sentences sharing an unbroken 6+-word run are flagged as the same rule
  regardless of what else surrounds it, even if their overall Jaccard
  similarity is low. Both signals are OR'd for sentence-level matching.

Headings are stripped before sentence-splitting (a heading has no
terminal punctuation, so left in place it glues onto the next real
sentence and can produce spurious matches — this was a real bug caught
during development).

**Known miss case**: a near-duplicate that differs by one word in a way
that breaks the contiguous run (e.g. "never delete a file" vs. "never
delete a **data** file") can still evade both signals if the resulting
fragments are each individually short. Lowering `MIN_SHARED_PHRASE_WORDS`
would catch more of these at the cost of more false positives elsewhere;
6 was chosen as a reasonable balance, not a proven optimum — retune per
corpus if this misses too much in practice.

## "Likely unnecessary content"

Two mechanical flags, both cheap and specific rather than an attempt at
general "unnecessariness" detection:

- **Movable sections**: any `background`/`examples` section (see above).
- **Generic filler phrasing** (`scripts/we_filler.py`): a sentence is
  flagged only if it is (a) **not an instruction** — contains none of
  must/never/always/do not/don't/only/should/required/ensure/run/use/
  call/pass/set/avoid/prefer and does not open with a base-form verb
  ("Check the log first.") — and (b) matches a hedge / pleasantry /
  rationale-only pattern ("This is useful because...", "Note that it's
  important to...", "Feel free to...", "Hope this helps", "this skill is
  designed to..."). Asymmetric on purpose: flagging a real instruction
  invites deleting it, while missing some filler only costs a few tokens,
  so unknown first words count as verbs (not flagged), and a pleasantry
  that carries a constraint ("Feel free to skip step 3 only when...") is
  never flagged. The smoke test holds this to a labelled fixture
  (`tests/fixtures/filler_sentences.json`): 0 instruction sentences
  flagged, >= 80% filler recall.

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

## Why the rewriting itself isn't automated

`optimize` only performs moves that are **lossless by construction**
(relocate text verbatim, leave a pointer) — nothing here rewrites prose,
shortens the description, or merges duplicate rules into one wording.
Those all require judging what's *actually* obvious/redundant/replaceable
in the "this content, in this real skill, for this audience" sense, which
a fixed rule can't do safely — the risk of a mechanical "shorten this"
transform silently dropping a load-bearing clause is exactly the failure
mode `test-function` exists to catch, and it's better caught by not doing
the risky transform automatically in the first place. See SKILL.md's
workflow step 3.

"Not automated" doesn't mean the judgment call can't be *structured* --
`suggest`/`apply-suggestion` package the same information above into an
explicit request/answer file pair instead of freeform editing, gated by
this repo's LLM-assist level. No new model call is introduced by this;
`test-function`/`test-routing` still run against the result exactly as
they would against a freehand edit. See `references/llm-assist.md`.
