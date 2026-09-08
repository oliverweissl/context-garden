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
scripts/benchmark list                                    # discovered fixtures
scripts/benchmark run --runner claude-code --repeat 5 [--task ID ...] [--component NAME ...]
scripts/benchmark analyze <results-dir>                    # regenerate summary.md
```

`run` spends real API usage (headless `claude -p`), capped by
`--max-budget-usd` (default $1.00/run). Results:
`benchmarks/results/<UTC timestamp>/{records.jsonl,summary.md}`.
