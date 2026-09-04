# context-slice (placeholder)

**Status:** specification not yet written. No `SKILL.md` exists here yet.

## Intent

Selects the smallest useful portion of a repository for the task currently being performed. Aims to give agents the relevant source, tests, configuration, and dependencies without loading or searching unnecessary parts of the codebase.

## What's here

Nothing implemented yet. This directory exists to reserve the Skill's place in the repository layout. Once a specification is supplied, this directory will contain:

```text
context-slice/
├── SKILL.md
├── agents/
│   └── openai.yaml
├── scripts/
├── references/
└── assets/
```

Do not add behavioral logic here until the specification lands. See the repository root [`README.md`](../../README.md) and [`docs/architecture.md`](../../docs/architecture.md) for context.
