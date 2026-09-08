# AGENTS.md (seedbank hot facts)

- Validate every Skill's structure with `scripts/validate-skills`.
  (source: docs/skill-development.md, scripts/validate-skills)
- Self-containment: a Skill must never reference a relative path outside
  its own directory, e.g. `../../benchmarks/...`.
  (source: docs/architecture.md, "Skill self-containment (critical rule)")
- Accept a weeder rewrite only if routing accuracy after >= before
  (minus a small tolerance) AND constraint preservation is 100%.
  (source: skills/weeder/SKILL.md, workflow step 7)
