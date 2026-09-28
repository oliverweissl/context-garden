# Benchmarks

Compares an agent with vs. without a Skill, on tasks built from this
repository (real injected bug, real oversized Skill, niche correctness
scenario). Metric and schema: `docs/benchmarking.md`.

## Layout

```text
harness/      fixture discovery/materialization, agent runners, verification, analysis
fixtures/     one dir per task (task.yaml + optional bug.patch / overlay/)
results/      generated output (gitignored)
```

## Fixtures

| task_id | component | level | tests |
|---|---|---|---|
| `pruner-file-tokenusage-bug` | pruner | file | locates the one relevant file instead of exploring broadly |
| `trellis-toy-solver-order-regression`* | trellis | repo | catches an order-of-accuracy regression a passing test misses |
| `compost-noisy-failure-cluster` | compost | repo | clusters 17 failures down to 2 root causes instead of reading raw output |
| `seedbank-recurring-facts` | seedbank | repo | pre-populated `AGENTS.md` avoids re-discovering scattered facts |
| `weeder-bloated-skill-audit`* | weeder | file | shrinks an oversized `SKILL.md` without losing a constraint |

\* `run_by_default: false` — skipped by a plain `scripts/benchmark run`
(needs `--task`/`--component` to run); see `docs/benchmarking.md`.

## Running

```bash
scripts/benchmark list                          # discovered fixtures
scripts/benchmark run --dry-run                 # randomised schedule + cost bound, runs nothing
scripts/benchmark run [--task ID ...] [--component NAME ...] [--arms ...] [--runs 10] [--seed 0]
scripts/benchmark run --resume <results-dir>    # continue an interrupted run
scripts/benchmark analyze <results-dir>         # regenerate summary.md / summary.json
scripts/benchmark plot <results-dir>            # benchmark.png (needs the `bench` extra)
```

`run` spends real API usage (headless `claude -p`): sessions = tasks ×
arms × runs, each capped by `--max-budget-usd` (default $1.00). Results:
`benchmarks/results/<UTC timestamp>/{schedule.json,records.jsonl,summary.md,summary.json}`.
Methodology (arms, randomisation, metrics, CIs): `docs/benchmarking.md`.
