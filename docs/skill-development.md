# Skill development

Read `skills/pruner/` end to end first: small `SKILL.md`, deterministic `scripts/`, a real smoke test.

## Layout

```text
skills/<name>/
├── SKILL.md        # required: name + description frontmatter, then the workflow an agent follows
├── bin/             # thin CLI wrapper(s), e.g. bin/compost
├── scripts/         # deterministic logic — this is where the real work happens
├── references/      # optional depth, loaded on demand (not always-loaded with SKILL.md)
└── tests/           # the Skill's own smoke test + fixtures
```

Only create the directories a Skill actually needs — most don't need `references/`.

## Rules

- Self-containment and progressive disclosure: [`architecture.md`](architecture.md). `scripts/validate-skills`
  is the single command that validates every Skill's structure (frontmatter, referenced files exist,
  no path outside the Skill; inline code and fenced blocks are exempt).
- Stdlib only (trellis may use numpy); never import a sibling Skill's code — vendor or bundle it.
  compost must stay Python 3.9-compatible.
- `SKILL.md` and printed handles invoke `<skill-dir>/bin/<name>`, never a bare command: Skill CLIs are not on PATH.
- The Skill's own `tests/smoke_test.sh` is what travels with it when copied out standalone; CI skips it.

## Adding a Skill

1. Implement under `skills/<name>/`: deterministic `scripts/` + a `SKILL.md` that tells an agent
   when to reach for it and what workflow to follow.
2. Add `tests/smoke_test.sh` + fixtures that exercise it end to end (`skills/pruner/tests/` is a fixture-driven example).
3. Run `scripts/validate-skills`.
4. Benchmark it against a baseline agent ([`benchmarking.md`](benchmarking.md)).
5. `scripts/build-skills` to package it for distribution.
