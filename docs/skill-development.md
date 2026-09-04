# Skill development

This document describes the conventions a Context Garden Skill follows. All five Skills are implemented: `seedbank`, `pruner`, `compost`, `trellis`, `weeder`.

## Layout

```text
skills/<name>/
├── SKILL.md
├── bin/            # thin CLI wrapper(s), e.g. `bin/compost`
├── scripts/
├── references/
├── tests/          # the Skill's own smoke test + fixtures (self-check)
└── validate.md      # how to manually validate the Skill
```

Only create the directories a given Skill actually needs — don't scaffold empty directories speculatively.

## Principles

- Keep `SKILL.md` small. It's the part loaded into context first and most often; everything that isn't needed to decide whether/how to invoke the Skill belongs elsewhere.
- Put deterministic operations in `scripts/`, not in prose instructions. If a step has one correct answer, it should be code, not something the model reasons through each time.
- Put detailed, optional knowledge in `references/`, loaded only when actually needed (progressive disclosure).
- `tests/skills/` covers repository-level structural validation (does every `SKILL.md` have the required frontmatter, does it stay self-contained). A Skill's own `tests/smoke_test.sh` + fixtures + `validate.md`, bundled inside the Skill directory, is its self-check — keep that so the Skill still validates itself when copied out on its own.
- Avoid duplicating shared implementation across Skills — see the self-containment rule below for how to reuse code without violating it.
- Optimize for progressive context loading: a Skill should cost little to have *available*, and more only once it's actually *used*.
- Make the Skill usable independently of the monorepo — assume an installer copies only `skills/<name>/`.

## Self-containment

A Skill must never depend on relative paths outside its own directory (e.g. `../../src/context_garden/...`). If it needs functionality from `context_garden.core`, choose one of:

1. **Bundle at build time** (preferred for portable Skills) — `scripts/build-skills` copies/vendors the needed modules into the Skill directory as part of producing a distributable artifact.
2. **Vendor manually** into the Skill's own `scripts/` if the dependency is small and stable.
3. **Declare an external runtime dependency** on the `context-garden` package, documented explicitly in the Skill's `SKILL.md`, for Skills that are fine assuming a Python environment with it installed.

`scripts/validate-skills` checks for violations of this rule.

## Implementing a new Skill (once specified)

1. Implement the Skill in isolation under `skills/<name>/`.
2. Add deterministic scripts for anything that doesn't need model judgment.
3. Add a `tests/smoke_test.sh` + fixtures inside the Skill's own directory, and a `validate.md` describing how to run it.
4. Bundle shared runtime if required (see above).
5. Validate self-containment with `scripts/validate-skills`.
6. Benchmark against a baseline agent using `benchmarks/` (see `docs/benchmarking.md`).
7. Package/release the Skill independently via `scripts/build-skills`.

Repeat per component. Do not implement a Skill ahead of its specification — see the repository root `README.md` for current status.
