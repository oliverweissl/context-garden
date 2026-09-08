# The routing and function proxies: what they do and don't guarantee

Neither `test-routing` nor `test-function` calls a model. Both are
deterministic, offline, explainable proxies — the same design choice
compost/seedbank/pruner/trellis all make for their
own approximated pieces (lexical relevance instead of embeddings, CI-
overlap instead of a formal hypothesis test, and so on). They're useful
for exactly what they measure, and should not be mistaken for the thing
they approximate.

## `test-routing`: lexical-overlap proxy, not model prediction

`we_routing.py`'s `relevance_score` is a symmetric Jaccard similarity
between a skill description's keywords and a prompt's keywords (same
keyword-extraction approach as pruner's lexical relevance scoring —
lowercased, `snake_case`/`camelCase` split into sub-words, stopwords
removed). `predict_trigger` picks whichever candidate description (the
skill under test, plus any `--competing` descriptions supplied) scores
highest; ties/all-zero resolve to "no prediction".

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
  descriptions, almost any prompt that shares even one keyword with the
  description "wins" by default (there's nothing to lose to). Always pass
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
skill's full reachable text (SKILL.md + every reference), and checks each
one's non-trivial keywords (>3 chars, common words excluded) appear
somewhere — at a >=60% coverage ratio by default — in the **after**
skill's full reachable text. A move to a reference file counts as
preserved; the check operates on everything reachable, not just
`SKILL.md` itself.

**What this proves**: a constraint's wording was not deleted outright —
the single most damaging and most common failure mode of a manual or
LLM-driven "let me tighten this up" edit (see `tests/smoke_test.sh`'s
worked example, where deleting an entire `## Rules` section is caught
immediately: 7/7 -> 5/7 preserved, with the missing sentences listed
verbatim).

**What this does NOT prove**: that the *meaning* survived a rewrite that
kept similar words but changed what they say (e.g. softening "never
relax the tolerance" into "avoid relaxing the tolerance where possible"
would likely still pass this coverage check, since the keywords overlap,
even though the constraint's actual force changed). Keyword coverage is a
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
