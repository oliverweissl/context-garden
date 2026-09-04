# Skill development

This document describes the conventions a future Context Garden Skill should follow. No Skill has been implemented yet — this is guidance for when a specification is supplied (see `docs/architecture.md` for why this repository is deliberately small right now).

## Layout

```text
skills/<name>/
├── SKILL.md
├── agents/
│   └── openai.yaml
├── scripts/
├── references/
└── assets/
```

Only create the directories a given Skill actually needs — don't scaffold empty `references/` or `assets/` directories speculatively.

## Principles

- Keep `SKILL.md` small. It's the part loaded into context first and most often; everything that isn't needed to decide whether/how to invoke the Skill belongs elsewhere.
- Put deterministic operations in `scripts/`, not in prose instructions. If a step has one correct answer, it should be code, not something the model reasons through each time.
- Put detailed, optional knowledge in `references/`, loaded only when actually needed (progressive disclosure).
- Keep tests outside the Skill, in `tests/skills/`. Distributed Skills should not carry test code.
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
3. Add tests under `tests/skills/`.
4. Bundle shared runtime if required (see above).
5. Validate self-containment with `scripts/validate-skills`.
6. Benchmark against a baseline agent using `benchmarks/` (see `docs/benchmarking.md`).
7. Package/release the Skill independently via `scripts/build-skills`.

Repeat per component. Do not implement a Skill ahead of its specification — see the repository root `README.md` for current status.
