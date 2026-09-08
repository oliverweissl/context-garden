---
name: weeder
description: Measure and reduce a Skill's always-loaded token footprint (SKILL.md description + core instructions) without regressing routing accuracy or losing instructions — use when a SKILL.md feels oversized, has rules repeated in multiple places, or has a vague/generic description that might mis-route. Treats Skills like software that gets compiled and optimized, with before/after measurement, not guesswork.
---

# weeder

Skills are read into context on every invocation — an oversized `SKILL.md`
is a permanent tax paid every time the skill triggers, whether or not that
session needed the tutorial/background/examples bloating it. This skill
measures that tax, mechanically removes the part of it that's pure
overhead (content that's already fully reachable via a reference file, not
lost), and validates — with executable checks, not eyeballing — that
routing and functional behavior didn't regress before you'd ever accept
the smaller version.

**Never optimize on token count alone.** A shorter `SKILL.md` that routes
worse or silently drops an instruction is a regression dressed up as a
win. Every step below produces evidence for the other two before/after
comparisons (tokens, routing, function) specifically so that trade isn't
made blind.

## Workflow

1. **Audit** the skill:
   ```
   python3 <this-skill-dir>/scripts/weeder.py audit <skill_dir>
   ```
   Reports, deterministically: token count per section (classified as
   `routing_metadata` / `mandatory_instructions` / `workflow` /
   `constraints` / `examples` / `background` / `external_reference`),
   duplicate content (a rule restated in multiple places — both whole
   repeated paragraphs and, separately, the same core sentence embedded in
   otherwise-different surrounding prose, which is the more common real
   shape of this problem), and likely-unnecessary content (generic filler
   phrasing, background/example sections eating into the always-loaded
   budget). **Read the whole thing** — the mechanical `optimize` step
   below only acts on part of what audit finds.

2. **Apply the mechanical, safe pass**:
   ```
   python3 <this-skill-dir>/scripts/weeder.py optimize <skill_dir> --out <skill_dir>-optimized
   ```
   Moves `background`/`examples` sections into `references/<slug>.md`
   files, replacing them in `SKILL.md` with a one-line pointer. This is
   lossless (every word survives, just relocated) and reversible, so it's
   safe to always apply — it never needs judgment. It does **not**
   shorten the description or touch duplicate rules; see step 3.

3. **Do the parts that need judgment, in the `-optimized` copy**:
   - **Shorten the routing description** using `audit`'s token count and
     the skill's actual purpose — cut generic phrasing
     ("this skill helps you with...", "use this whenever you need..."),
     keep concrete trigger nouns/verbs a real prompt would contain.
   - **Consolidate duplicate rules**: `audit`'s duplicate groups show every
     restatement of the same rule — pick the clearest wording, state it
     once (in the section where an agent following the workflow would
     actually need it), delete the rest.
   - **Remove genuinely obvious/general instructions** flagged under
     "likely unnecessary content" — but only ones that are actually
     obvious to a capable agent; don't cut something just because it's
     short.

   None of this is mechanically automated — see `references/classification.md`
   for why. How much of the judgment step gets structured for you as an
   explicit request/answer file, vs. you editing the copy directly, is
   controlled by this repo's LLM-assist level (`none` default, `slight`,
   `lot` — resolved from `--assist`, `$CONTEXT_GARDEN_LLM_ASSIST`, or
   `.context-garden/config.yaml`'s `llm_assist.level`; see
   `references/llm-assist.md` for what each level structures and why
   this never involves a second model call):
   ```
   python3 <this-skill-dir>/scripts/weeder.py suggest <skill_dir> --assist slight --json
   ```
   At `none` this reports zero requests — do step 3 freehand. Otherwise,
   write your answer to a JSON file (shape in the command's own output
   and `references/llm-assist.md`) and apply it:
   ```
   python3 <this-skill-dir>/scripts/weeder.py apply-suggestion <skill_dir> --answer <answer.json> --out <skill_dir>-optimized
   ```
   This only applies the answer — it doesn't validate it. Steps 4-7
   below hold an assist-drafted answer to the same bar as a freehand
   edit; it gets no free pass.

4. **Validate routing** before trusting the rewrite:
   ```
   python3 <this-skill-dir>/scripts/weeder.py test-routing <skill_dir> \
     --examples <examples.json> [--competing <other_skill_dir> ...]
   ```
   `examples.json`: `{"positive": [prompt, ...], "negative": [prompt, ...],
   "ambiguous": [{"prompt":, "expected": "trigger"|"no_trigger"|"either"}]}`.
   If none exists for this skill, **write one** (5-10 prompts covering the
   skill's real triggers, a few clearly-unrelated prompts, and any
   genuinely borderline case) — this is the routing test suite, treat it
   like one. Pass `--competing` with sibling skills' directories whenever
   they exist; without any competitor, almost any nonzero keyword overlap
   "wins" trivially, which tells you much less. Run this on **both** the
   original and the `-optimized` copy and compare — see
   `references/routing-heuristic.md` for exactly what this proxy does and
   doesn't predict about real model routing.

5. **Validate function**:
   ```
   python3 <this-skill-dir>/scripts/weeder.py test-function <skill_dir> <skill_dir>-optimized
   ```
   Extracts every imperative/constraint sentence ("never...", "must...",
   "do not...") from the original and checks each one's key terms still
   appear somewhere in the rewrite (SKILL.md or any reference — a move
   doesn't count as loss). A constraint reported MISSING means content was
   actually deleted, not relocated — treat that as a hard blocker, not a
   warning.

6. **Get the combined report**:
   ```
   python3 <this-skill-dir>/scripts/weeder.py diff <skill_dir> <skill_dir>-optimized \
     --examples <examples.json> --competing <other_skill_dir> ...
   ```
   One before/after report: token breakdown, always-loaded reduction %,
   routing accuracy before/after, constraint-preservation ratio.

7. **Accept only if**: routing accuracy after >= before minus a small
   tolerance (a couple percentage points of a lexical proxy is noise, not
   signal — see `references/routing-heuristic.md`), AND constraint
   preservation is 100% (anything less needs the missing constraint
   restored, not accepted as a rounding error). If both hold, replace the
   original with the `-optimized` copy. If either doesn't, the rewrite in
   step 3 needs another pass — don't lower the bar to make a bad result
   pass.

## Modes reference

See `references/modes.md`.
