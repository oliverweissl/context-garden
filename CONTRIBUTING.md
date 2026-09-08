# Contributing

```bash
git clone <repo-url> && cd context-garden
pip install -e ".[dev]"
pytest
```

Requires Python >= 3.11.

## Layout

- `skills/<name>/` — one dir per Skill, self-contained (see `docs/architecture.md`).
- `tests/` — repo-level tests. Each Skill also has its own `tests/smoke_test.sh`.
- `benchmarks/` — evaluation harness + fixtures.
- `docs/` — architecture, Skill layout, benchmarking.

## Before opening a PR

1. Open an issue first for substantial Skill work.
2. Keep infra changes and Skill changes in separate PRs.
3. Add/update tests for anything under `benchmarks/harness/`.
4. Run `pytest` and `scripts/validate-skills`.
5. Don't add functionality speculatively (`docs/architecture.md`).

## Issues

Include what you expected, what happened, and how to reproduce it.
