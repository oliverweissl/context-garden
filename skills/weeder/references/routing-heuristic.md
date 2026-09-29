# The routing and function proxies: what they do and don't guarantee

Neither `test-routing` nor `test-function` calls a model. Both are
deterministic, offline proxies: useful for exactly what they measure, not
a substitute for the thing they approximate.

## `test-routing`: lexical-overlap proxy, not model prediction

`we_routing.py`'s `relevance_score` is a coverage score: the fraction of
a prompt's keywords that the skill description contains
(|prompt ∩ description| / |prompt|; same keyword-extraction approach as
pruner's lexical relevance scoring — lowercased, `snake_case`/`camelCase`
split into sub-words, stopwords removed — plus light suffix stripping so
"files"/"file" and "deleting"/"delete" match). Unlike a symmetric Jaccard
score, it doesn't penalise a longer description for words the prompt
doesn't use, so adding concrete trigger vocabulary never lowers a score.
`predict_trigger` picks whichever candidate description (the skill under
test, plus any `--competing` descriptions supplied) scores highest; a
best score below `MIN_SCORE` (0.2) resolves to "no prediction".

**What this predicts reasonably well**: whether a description contains
the concrete nouns/verbs a real prompt about this skill's domain would
use, relative to competing descriptions that also contain domain words. A
description that's all generic phrasing ("helps you with various tasks")
carries almost no keyword signal and will lose to any competitor with
real domain vocabulary — which is exactly UC3's failure mode, and exactly
what this catches (see the UC3 worked example in `tests/smoke_test.sh`
where a vague description loses a real trigger prompt to a genuine
competing skill, and a tightened one wins it).

**What this does NOT predict**: an actual model's routing decision, which
reasons about intent, ambiguity, and multi-skill applicability in ways no
keyword-overlap heuristic captures. Two failure directions to watch for:

- **False confidence without competitors.** With no `--competing`
  descriptions, any prompt clearing the `MIN_SCORE` overlap floor "wins"
  by default (there's nothing to lose to). Always pass
  real sibling-skill directories via `--competing` when they exist; a
  100% accuracy score against zero competitors means much less than the
  same score against five real ones.
- **Missed near-synonyms.** "compact this log" vs. a description that
  says "summarize output" shares no keywords at all despite meaning
  roughly the same thing to a real reader. This proxy will score that 0.
  If a description passes this test poorly but you're confident a real
  agent would still route correctly (or vice versa), trust the manual
  check — spot-check genuinely ambiguous/borderline prompts with a real
  agent session before finalizing a rewrite — the same manual check every
  skill in this suite needs for its approximated pieces.

**Measuring the proxy against real routing.** The context-garden repo
ships a manual `validate-routing` script (top-level `scripts/`, not part
of this skill, never run in CI) that installs the skill plus its
competitors into a temp project's `.claude/skills/`, runs each example
prompt through `claude -p --output-format stream-json --verbose`, and
records which `Skill` tool call actually fires. It reports real-routing
accuracy, this proxy's accuracy on the same prompts, and a real-vs-proxy
confusion table — use it to find prompts where the proxy is wrong before
trusting it on a rewrite. It spends the user's own Claude Code usage
(about one short turn per prompt); weeder itself stays offline.

**Tolerance for accepting a change**: routing accuracy after >= before
minus a couple percentage points is noise-level for this proxy given
typical example-set sizes (5-15 prompts, where one flipped prediction
already swings the percentage by double digits) — don't chase a 1-example
regression as if it were a real signal, but do investigate anything
larger, and always investigate a *specific prompt* that flips from
correct to incorrect, not just the aggregate number.

## `test-function`: constraint-coverage proxy, not a behavioral eval

`we_constraints.py` extracts sentences matching an imperative/constraint
pattern (`never`, `always`, `must`, `do not`, `required`, `critical`,
`cannot`, `shall not`, `prohibited`, `forbidden`) from the **before**
skill's full reachable text (SKILL.md + every reference), one per
sentence (hard-wrapped lines within a paragraph or list item are joined
first), and checks each one is still stated within a **single sentence**
of the **after** skill's full reachable text: that sentence must contain
the constraint's non-trivial keywords (>3 chars, common words excluded)
at a >=60% coverage ratio by default, every negation/modal marker it uses
(`never`, `always`, `not`/`don't`/`do not`, `must`, `only`, ...), and the
same polarity (negated or not). So deleting a rule, flipping "Never X" to
"Always X", or "must X" to "must not X" is reported MISSING even though
the vocabulary survives elsewhere. A move to a reference file counts as
preserved; the check operates on everything reachable, not just
`SKILL.md` itself.

**What this proves**: a constraint's wording was not deleted outright —
the single most damaging and most common failure mode of a manual or
LLM-driven "let me tighten this up" edit (see `tests/smoke_test.sh`'s
worked example, where deleting an entire `## Rules` section is caught
immediately: 7/7 -> 5/7 preserved, with the missing sentences listed
verbatim).

**What this does NOT prove**: that the *meaning* survived a rewrite that
kept similar words but changed what they say (e.g. rephrasing "always
confirm first" as "confirm first where practical" drops the marker and is
caught, but a subtler hedge added alongside the original markers would
still pass). Keyword coverage is a
floor, not a ceiling — read the actual diff for anything `test-function`
reports as preserved before trusting that the *rule*, not just its
vocabulary, survived.

**Extraction is pattern-based, not universal.** A constraint phrased
without any of the trigger words above (e.g. "Confirmation is needed
before deletion" — no "must"/"never"/"required"/etc.) won't be extracted
at all, so it's silently absent from the *before* count rather than
flagged as at-risk. When authoring or reviewing a skill's constraints,
prefer the explicit imperative words this extractor looks for — it's also
just clearer writing for a human or agent reader either way.
