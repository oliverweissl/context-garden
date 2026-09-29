# Contributing

Python >= 3.11 (system python has no pytest):

```bash
pip install -e '.[dev]'
pytest
ruff check .
python scripts/validate-skills
bash skills/<name>/tests/smoke_test.sh   # offline; CI runs these too
```

## Before opening a PR

1. Open an issue first for substantial Skill work.
2. Keep infra changes and Skill changes in separate PRs.
3. Add/update tests for anything under `benchmarks/harness/`.
4. Don't add functionality a benchmark hasn't shown to help ([`docs/architecture.md`](docs/architecture.md#design-principles)).
