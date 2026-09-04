# Contributing to Context Garden

Thanks for your interest in contributing. The repository is in early bootstrap: infrastructure and placeholders exist, but the five planned Skills (`context-cache`, `context-slice`, `tracefold`, `numerical-sentinel`, `skill-debloater`) are not yet implemented. Please open an issue before starting substantial work on a Skill so scope and specification can be agreed on first.

## Development setup

```bash
git clone <repo-url>
cd context-garden
pip install -e ".[dev]"
pytest
```

Requires Python >= 3.11.

## Repository layout

- `src/context_garden/` — shared runtime library (event schema, artifact storage, provenance, token accounting, local state).
- `skills/<name>/` — one directory per distributable Agent Skill. Must remain self-contained (see below).
- `tests/` — all repository tests. Never shipped inside a Skill.
- `benchmarks/` — evaluation fixtures, harness, and results.
- `docs/` — architecture and process documentation.
- `scripts/` — repository-level tooling (`validate-skills`, `build-skills`, `benchmark`).

## Skill self-containment rule

Code under `skills/<skill-name>/` must never reach outside its own directory (e.g. `../../src/context_garden/...`). Skill installers may copy only that single directory. If a Skill needs shared functionality, it must be bundled at build time, vendored into the Skill's `scripts/`, or declared as a documented external `context-garden` runtime dependency. Run `scripts/validate-skills` to check this.

## Making changes

1. Fork/branch from `main`.
2. Keep changes scoped — infrastructure changes and Skill changes should generally be separate PRs.
3. Add or update tests under `tests/` for anything in `src/`.
4. Run `pytest` and `scripts/validate-skills` locally before opening a PR.
5. Describe the "why" in your PR description, not just the "what".

## Code style

- Prefer small, deterministic, testable functions over prompt-driven logic wherever the behavior can be made deterministic.
- Keep `src/context_garden` dependency-light — parts of it may later be bundled directly into individual Skills.
- Don't add functionality speculatively. See `docs/architecture.md` for the project's core principles.

## Reporting issues

Open a GitHub issue with as much context as possible: what you expected, what happened, and how to reproduce it.
