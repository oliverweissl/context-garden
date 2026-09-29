# The routing and function proxies: what they do and don't guarantee

Neither `test-routing` nor `test-function` calls a model. Both are
deterministic, offline proxies: useful for exactly what they measure, not
a substitute for the thing they approximate.

## `test-routing`: lexical-overlap proxy, not model prediction

`we_routing.py`'s `relevance_score` is a coverage score: the fraction of
a prompt's keywords that the skill description contains
(|prompt ∩ description| / |prompt|; lowercased, `snake_case`/`camelCase`
split into sub-words, stopwords removed, light suffix stripping so
"files"/"file" and "deleting"/"delete" match). It doesn't penalise a
longer description for words the prompt doesn't use, so adding concrete
trigger vocabulary never lowers a score. `predict_trigger` picks
whichever candidate description (the skill under test, plus any
`--competing` descriptions) scores highest; a best score below
`MIN_SCORE` (0.2) resolves to "no prediction".

**What this predicts reasonably well**: whether a description contains
the concrete nouns/verbs a real prompt about this skill's domain would
use, relative to competing descriptions. An all-generic description
("helps you with various tasks") carries almost no keyword signal and
loses to any competitor with real domain vocabulary.

**What this does NOT predict**: an actual model's routing decision, which
reasons about intent, ambiguity, and multi-skill applicability. Two
failure directions:

- **False confidence without competitors.** With no `--competing`
  descriptions, any prompt clearing the `MIN_SCORE` floor "wins" by
  default. A 100% score against zero competitors means much less than
  the same score against five real ones.
- **Missed near-synonyms.** "compact this log" vs. a description that
  says "summarize output" shares no keywords and scores 0. If the proxy
  and your judgment disagree, spot-check the borderline prompts with a
  real agent session before finalizing a rewrite.

For real-model validation, the context-garden repo's top-level
`scripts/validate-routing` (not shipped with this skill) compares real
routing against this proxy on the same prompts.

Beyond the aggregate number, always investigate a *specific prompt* that
flips from correct to incorrect.

## `test-function`: constraint-coverage proxy, not a behavioral eval

`we_constraints.py` extracts sentences matching an imperative/constraint
pattern (`never`, `always`, `must`, `do not`, `required`, `critical`,
`cannot`, `shall not`, `prohibited`, `forbidden`) from the **before**
skill's full reachable text (SKILL.md + every reference), one per
sentence, and checks each one is still stated within a **single sentence**
of the **after** skill's full reachable text: that sentence must contain
the constraint's non-trivial keywords (>3 chars, common words excluded)
at a >=60% coverage ratio by default, every negation/modal marker it uses
(`never`, `always`, `not`/`don't`/`do not`, `must`, `only`, ...), and the
same polarity. So deleting a rule, flipping "Never X" to "Always X", or
"must X" to "must not X" is reported MISSING even though the vocabulary
survives elsewhere. A move to a reference file counts as preserved.
Hyphenated forms like "always-loaded" are not treated as constraints.

**What this proves**: a constraint's wording was not deleted outright —
the most common and most damaging failure of a "let me tighten this up"
edit.

**What this does NOT prove**: that the *meaning* survived a rewrite that
kept similar words. E.g. rephrasing "always confirm first" as "confirm first
where practical" drops the marker and is caught, but a subtler hedge
added alongside the original markers would still pass. Read the actual diff
for anything reported as preserved before trusting that the *rule*, not
just its vocabulary, survived.

**Extraction is pattern-based, not universal.** A constraint phrased
without any trigger word (e.g. "Confirmation is needed before deletion" —
no "must"/"never"/"required"/etc.) won't be extracted at all, so it's
silently absent from the *before* count rather than flagged as at-risk. Prefer the explicit imperative words when
writing constraints.
