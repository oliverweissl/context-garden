# Architecture

## Repository layout

```text
src/         shared implementation source
skills/      distributable Agent Skills
tests/       repository tests, never shipped with Skills
benchmarks/  evaluation infrastructure and datasets
```

`src/context_garden` is a normal installable Python package that provides shared infrastructure — artifact storage, provenance, event recording, token accounting, and local state — for use while *developing* Skills, and optionally as a runtime dependency for Skills that choose not to bundle it.

`skills/<skill-name>/` directories are the units actually distributed to end users through Git-based Skill installers. Every one of them must eventually be independently installable — see the critical rule below.

`tests/` covers `src/context_garden` and repository-level structural validation of Skills. It is never packaged or copied alongside a Skill.

`benchmarks/` hosts the evaluation harness and, eventually, fixtures and results comparing a baseline agent against agent + Context Garden component.

## Critical rule: Skill self-containment

A Skill must never require relative access such as `../../src/context_garden/`, because installers may copy only the individual Skill directory. Shared functionality used by a Skill must eventually either:

1. be bundled into the Skill at build time (`scripts/build-skills`), preferred for portable Skills,
2. be vendored directly into the Skill's own `scripts/`, or
3. be declared as a documented external `context-garden` runtime dependency.

`scripts/validate-skills` is expected to check for violations of this rule (e.g. references to paths outside a Skill's own directory).

## Core project principles

### 1. Context is a scarce resource

Every persistent instruction, tool result, source chunk, and generated message competes for model attention. Treat context budget as a real constraint, not an afterthought.

### 2. Reduce context, not evidence

Compression must never destroy access to the original evidence. A compact summary is only useful if the raw material behind it remains retrievable — this is why artifacts are content-addressed and provenance records exist.

### 3. Progressive disclosure

Only expose information when it becomes relevant. Skills should keep `SKILL.md` small, push deterministic work into scripts, and push optional depth into `references/` that's loaded on demand.

### 4. Self-contained Skills

Installed Skills cannot assume the surrounding Git repository exists. Design and test accordingly.

### 5. Deterministic machinery over prompt instructions

Use code — not prose instructions to a model — for parsing, hashing, accounting, indexing, and validation whenever possible. Prompts are for judgment calls; code is for anything that has one correct answer.

### 6. Optimize verified-result efficiency

The primary long-term metric for the whole project:

```text
verified successful results
───────────────────────────
total tokens consumed
```

Not raw token reduction — token reduction that doesn't preserve or improve correctness is not a win.

### 7. Measure before optimizing

Context Garden should eventually quantify the waste it claims to remove, via the benchmark harness in `benchmarks/`, comparing baseline agents against agents using individual Context Garden components.

## The shared event model

All Skills record context-related activity using one common schema (`context_garden.core.events.Event`), so that activity from different Skills stays comparable. Supported event classes: `context_access`, `context_generated`, `context_promoted`, `context_invalidated`, `tool_output`, `verification`, `skill_execution`. The schema carries a `resource`, a `purpose`, an estimated `cost`, an optional `result` (e.g. an artifact id), and is extensible via arbitrary extra fields.

## The `.context-garden/` runtime directory

Repositories that use Context Garden Skills accumulate project-local state under:

```text
.context-garden/
├── config.yaml
├── context.db
├── artifacts/
├── cache/
└── index/
```

`config.yaml` is the only file in this directory meant to be shared/committed; everything else is generated local state and should be gitignored (see the root `.gitignore`).
