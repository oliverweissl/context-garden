# Validating weeder

weeder's job is: measure a Skill's always-loaded token cost
accurately, mechanically remove the part of that cost that's pure
overhead without losing any content, and give routing/functional evidence
before any rewrite is trusted — never trading tokens for correctness
silently. Validation checks four things: **measurement correctness**
(does `audit` classify/count/dedupe accurately — checked here against
this tool's OWN real SKILL.md, not just a synthetic fixture, since that's
where measurement bugs actually surfaced during development), **mechanical
safety** (does `optimize` ever lose content — it must not, by
construction), **proxy honesty** (do the routing/function checks catch
real regressions and admit what they can't see), and **self-consistency**
(the spec's own recommendation: use this suite's other four skills, and
weeder itself, as the first real optimization corpus).

## 1. Automated checks

```
bash tests/smoke_test.sh
```

Expected: `ALL CHECKS PASSED`, 35/35 checks, against
`tests/fixtures/bloated_skill` (deliberately combines all three spec UCs)
plus `bloated_skill_tightened` and `competing_skill` (for UC3). Covers:
oversized background/examples sections correctly flagged (UC1); the same
rule restated in 4 different places, worded differently each time,
correctly clustered into one finding (UC2); a vague description correctly
losing a real trigger prompt to a genuine competing skill, and a
tightened description correctly winning it (UC3); the mechanical
`optimize` pass being fully lossless (`test-function` reports 100%
preserved); `test-function` actually catching a real, deliberately
introduced constraint deletion (not just confirming the safe case); clean
error handling on a missing directory; `suggest`'s assist level correctly
gating 0/1/3 structured requests; `apply-suggestion` correctly
consolidating a 4-way duplicate down to one occurrence rather than
repeating it (see §7); the unchanged `test-function` gate still catching
an imperfect assist-drafted answer, not waving it through; and
`llm_assist.level` config resolution (flag > env var >
`.context-garden/config.yaml` > default).

## 2. Real-world verification: run it on this suite's other four skills

Per the spec's own Phase 5 recommendation ("use the preceding projects
themselves as the first optimization corpus") — done here against
compost, seedbank, pruner, trellis, and weeder itself:

```
for skill in compost seedbank pruner trellis weeder; do
  python3 scripts/weeder.py audit ../$skill
done
```

Results (measured, not illustrative):

| skill | always-loaded | movable | duplicate groups found |
|---|---|---|---|
| compost | 751 tok | 0 | 1 (repetitive template phrasing across 4 profile-detection rules in a reference doc — legitimate parallel structure, correctly left for human judgment, not auto-consolidated) |
| seedbank | 1172 tok | 0 | 1 (33% similarity, SKILL.md vs. a reference doc — borderline, plausibly acceptable restatement) |
| pruner | 998 tok | 0 | 1 (low similarity, likely acceptable) |
| trellis | 1574 tok | 142 tok ("Why numpy" background section) | 0 |
| weeder | 1529 tok | 75 tok ("Modes reference", classified `external_reference` — below the move threshold by design, see §4) | 2 |

Ran `optimize` + `test-function` on trellis (the clearest
candidate): moved the 142-token "Why numpy" section to
`references/why-numpy.md`, always-loaded tokens 1574 -> 1440 (an 8.5%
reduction from the mechanical pass alone), `test-function` confirmed
4/4 constraints preserved. Ran `test-function` on weeder against
itself (identity check, since nothing was mechanically movable): 8/8
constraints preserved, confirming the extractor doesn't false-positive
against its own text either.

**Takeaway**: the first four skills in this suite were already reasonably
disciplined (no huge tutorial/background sections, built with progressive
disclosure in mind from the start) — `optimize`'s biggest lever
(background/examples sections) mostly found little to move, which is
itself a useful, honest signal (not every skill needs debloating, and
this tool should say so rather than manufacture a reduction). The
duplicate-detection and filler-phrase flags found genuinely interesting,
non-obvious candidates (see §5) worth a human/agent judgment call, which
is exactly the division of labor this tool is designed around — it did
not unilaterally rewrite any of the four other skills; that decision is
left to whoever is asked to apply the findings.

## 3. Fidelity checks against the spec's acceptance criteria

| Criterion (from spec) | How to check | Status |
|---|---|---|
| Token accounting | `audit`'s per-section + reference-file token counts | met |
| Structural classification | `we_parse.classify_section` — 6 categories by heading keyword | met |
| Duplication detection | `we_dupes.py`, two granularities (paragraph + sentence/phrase) | met |
| Progressive-disclosure refactoring | `optimize` mechanically moves background/examples to `references/*.md` | met |
| Before/after diff | `weeder diff` — token/routing/function report matching the spec's own output example format | met |
| Routing test harness | `test-routing`, positive/negative/ambiguous examples + optional competing descriptions | met (documented lexical-proxy limitations, not real-model routing) |
| Functional regression harness | `test-function`, constraint-extraction + coverage check | met (documented keyword-coverage-proxy limitations, not a behavioral eval) |
| Refuse optimization when quality degrades materially | `test-routing --min-accuracy` / `test-function --min-ratio` exit nonzero; SKILL.md's workflow step 7 states the acceptance rule explicitly (routing >= before - tolerance AND constraints 100% preserved) | met |

## 4. Manual behavioral check (does an agent actually stop over-cutting)

Automated checks prove the CLI's measurement/mechanical/proxy logic is
correct; they don't prove an agent asked to "shrink this skill" will
actually run the validation steps rather than just eyeballing a rewrite.
Do this once per significant `SKILL.md` change:

1. Ask an agent (skill not installed) to shorten an oversized `SKILL.md`.
   Note whether it removes anything that reads as a real constraint,
   whether it checks the result against anything, and whether it declares
   success once the file is shorter.
2. Install `weeder/`, repeat. Confirm it runs `audit` first
   (not just eyeballing), applies `optimize`'s mechanical pass before
   attempting any manual rewrite, and — critically — actually runs
   `test-function`/`test-routing` before declaring the rewrite done, not
   just after being asked to check. Confirm it does **not** lower
   `--min-ratio`/`--min-accuracy` to force a failing rewrite to pass,
   which would just be this tool's own version of UC2 (relaxing the check
   instead of fixing the problem) — if that happens, it's a
   workflow/prompt issue to fix, not something to accept.

## 5. Bugs this tool's own SKILL.md caught during development

Directly caused by following the spec's advice to use the suite itself as
the first corpus — none of these were caught by the synthetic fixture
alone, only by running against real, organically-written prose:

1. **`always_loaded_tokens` excluded background/examples sections even
   *before* they were moved.** Since SKILL.md loads in full regardless of
   how a section is classified, this made `optimize`'s reported reduction
   read as 0% even when 466 real tokens had just been relocated to
   references (caught on the synthetic fixture, but a case that any real
   skill with an unmoved background section would also have hit
   silently). Fixed by making `always_loaded_tokens` = the full file, and
   `movable_tokens` a separate, honestly-named figure for what's not yet
   on-demand but could be.
2. **Fenced code blocks weren't stripped before duplicate/filler
   detection.** Running `weeder audit` on weeder's own SKILL.md
   (which, like most of this suite's skills, shows CLI usage in fenced
   ` ``` ` blocks throughout its Workflow section) produced a spurious
   5-occurrence "duplicate" purely from shared `python3 .../weeder.py`
   command-syntax boilerplate across five different subcommand examples —
   not a repeated rule at all. Fixed by stripping fenced/inline code
   before both paragraph- and sentence-level splitting (`we_text.py`).
3. **Sentence-splitting glued markdown headings onto the next real
   sentence** (a heading has no terminal punctuation), which both
   polluted word counts and could spuriously phrase-match two unrelated
   sections that happened to both open with a heading. Fixed by stripping
   heading lines before sentence-splitting.
4. **Whole-sentence Jaccard alone missed the classic UC2 shape.** A rule
   restated with different surrounding justification each time (this
   suite's `bloated_skill` fixture: "never delete a data file without
   confirmation" appearing 5 times, each with different trailing
   elaboration) diluted below any reasonable Jaccard threshold once the
   elaboration was long enough. Fixed by adding a contiguous-word-run
   signal (`longest_common_run`) OR'd with Jaccard for sentence-level
   matching specifically — this is the fix that took the fixture's
   detected-occurrence count from 1 to 4 of the 5 actual repeats (the 5th
   differs by one word — "a file" vs. "a **data** file" — in a way that
   breaks the contiguous run; see known limitations below).

## 6. Known limitations (accepted for MVP, revisit if they cause failures)

- **Rewriting prose is never automated** — `optimize` only performs
  lossless relocation. Description-shortening and duplicate-rule
  consolidation are always left to agent/human judgment (see
  `references/classification.md` for why). This is a scope boundary, not
  a gap.
- **`optimize`'s default move categories are `background`/`examples`
  only** — `external_reference` sections (already lightweight pointers)
  are deliberately excluded, which is why running `optimize` on
  weeder's own "Modes reference" section reports nothing
  movable even though `audit` counts its 75 tokens as `movable_tokens`.
  Pass a wider category set explicitly if a particular skill's
  `external_reference` sections are actually large enough to be worth
  relocating.
- **Both proxy checks have documented failure modes** — see
  `references/routing-heuristic.md` in full. Routing: no signal on
  near-synonym prompts that share no keywords; near-100% accuracy against
  zero `--competing` skills means little. Function: proves wording wasn't
  *deleted*, not that meaning survived a *rewrite* that kept overlapping
  keywords but changed what they assert; extraction only catches
  constraints phrased with an explicit imperative trigger word.
- **A near-duplicate that differs by one word in the wrong place can
  still evade both duplicate-detection signals** — see bug #4 above and
  `references/classification.md`'s "known miss case".
- **Filler-phrase detection can false-positive on quoted examples** — if
  a skill's own prose quotes a phrase like "this skill helps you with..."
  as an illustration of what to avoid (as this skill's own SKILL.md does,
  in its workflow step 3), the blocklist match fires on the quotation
  itself, not real usage. No fix attempted — distinguishing "quoted as an
  example" from "actually used" needs real language understanding, which
  this tool deliberately doesn't have.
- **A filler-phrase sentence can also independently match the
  constraint-extraction pattern** (e.g. "As an AI assistant, it's
  important to note that you should **always** be careful..." contains
  "always") — removing it via `unnecessary_removal` will then correctly
  trip `test-function`, since that sentence's wording really did disappear
  from the document. This is the two proxies disagreeing about the same
  sentence for legitimate, separate reasons, not a bug in either; treat a
  MISSING report as a reason to reconsider the removal, not to override it.
- **A multi-way duplicate consolidation rarely reaches 100% preservation
  on the first drafted answer** — see §7 and
  `references/llm-assist.md`'s "why consolidating a real multi-way
  duplicate rarely hits 100%" section. This is the coverage proxy applying
  the same standard to an assist-drafted answer as to a freehand edit, not
  a gap specific to `apply-suggestion`.

## 7. LLM-assist levels: structuring step 3, not automating it

`suggest`/`apply-suggestion` (added after the checks above) don't call any
model — they package the same information a freehand step-3 edit already
uses (`audit`'s description/duplicate_groups/likely_unnecessary) into an
explicit request/answer file pair, gated by an opt-in assist level
(`none` default, `slight`, `lot`; see `references/llm-assist.md`). Smoke
test coverage: the level correctly gates 0/1/3 structured requests;
`apply-suggestion` correctly implements "state it once, delete the rest"
for a duplicate group (an earlier draft of this feature replaced *every*
occurrence with identical text instead, which would have turned a
near-duplicate into an exact one — caught by running the round trip
against this suite's own fixture before writing the smoke-test assertion,
not by the assertion itself); the unchanged `test-function`/`test-routing`
gate still runs against `apply-suggestion`'s output and correctly refuses
an imperfect drafted answer; and `llm_assist.level` config resolution
(`--assist` flag > `$CONTEXT_GARDEN_LLM_ASSIST` >
`.context-garden/config.yaml` > `none`) is exercised in both directions
(each source overriding the one below it).
