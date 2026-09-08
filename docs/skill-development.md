# Skill development

A Skill is one self-contained directory under `skills/<name>/` that an
agent installs on its own — the whole point of `docs/architecture.md`'s
self-containment rule. `skills/pruner/` is a good one to read end to end
first: small `SKILL.md`, deterministic `scripts/`, a real smoke test.

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

- Keep `SKILL.md` small — deterministic logic goes in `scripts/`, optional depth in `references/`.
  This is the "progressive disclosure" principle from `docs/architecture.md`: an agent pays for
  `SKILL.md` on every use, but `references/*.md` only when it actually needs that depth.
- Self-contained: never a relative path outside the Skill's own directory. An installer may copy
  only `skills/<name>/`, so anything the Skill needs (shared code included) must live inside it.
  Checked by `scripts/validate-skills`.
- No duplicated shared implementation across Skills — vendor or bundle instead of importing a
  sibling Skill's code.
- `tests/skills/` covers repo-level structural checks (naming, frontmatter, self-containment);
  the Skill's own `tests/smoke_test.sh` is its functional self-check, and is what travels with it
  when it's copied out standalone into another project.

## Adding a Skill

1. Implement under `skills/<name>/`: deterministic `scripts/` + a `SKILL.md` that tells an agent
   when to reach for it and what workflow to follow.
2. Add `tests/smoke_test.sh` + fixtures that exercise it end to end (see `skills/pruner/tests/`
   for a fixture-driven example — it's caught real bugs that isolated unit tests missed).
3. Run `scripts/validate-skills` to check self-containment and structure.
4. Benchmark it against a baseline agent (`docs/benchmarking.md`) — the whole reason a Skill
   earns a place in this repo is a measured win, not an assumed one.
5. `scripts/build-skills` to package it for distribution.
